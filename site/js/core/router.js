// Hash router. URLs look like "#/team/1218?season=2025&model=epa". The query carries everything
// needed to reproduce a view.
//
//   parse(hash) -> {view, path: string[], query: Record<string,string>}
//   link(view, path?, query?) -> "#/team/1218?season=2025"
//   navigate(view, path, query, {replace, silent}?)
//   current() -> the route being shown
//   start(routes, opts) -> routes: {teams: () => import('../views/teams.js'), ...}
//
// A view module exports `render(ctx)` (sync or async) and may return a cleanup function.
// ctx = {root, route, setTitle(s), loading(msg), isCurrent(), signal}.

function dec(s) {
  try { return decodeURIComponent(s.replace(/\+/g, ' ')); } catch (e) { return s; }
}

export function parse(hash) {
  let h = String(hash || '');
  if (h.startsWith('#')) h = h.slice(1);
  const qi = h.indexOf('?');
  const pathPart = qi >= 0 ? h.slice(0, qi) : h;
  const qs = qi >= 0 ? h.slice(qi + 1) : '';
  const segs = pathPart.split('/').filter((s) => s !== '').map(dec);
  const query = {};
  for (const kv of qs.split('&')) {
    if (!kv) continue;
    const ei = kv.indexOf('=');
    const k = dec(ei >= 0 ? kv.slice(0, ei) : kv);
    const v = ei >= 0 ? dec(kv.slice(ei + 1)) : '';
    if (k && !(k in query)) query[k] = v;
  }
  return { view: segs[0] || '', path: segs.slice(1), query };
}

const enc = (s) => encodeURIComponent(String(s)).replace(/%3A/gi, ':').replace(/%2C/gi, ',');

export function link(view, path = [], query = {}) {
  const segs = [view, ...(Array.isArray(path) ? path : path === undefined || path === null ? [] : [path])]
    .filter((s) => s !== '' && s !== undefined && s !== null);
  let h = '#/' + segs.map(enc).join('/');
  const qs = Object.entries(query || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${enc(k)}=${enc(v)}`)
    .join('&');
  if (qs) h += '?' + qs;
  return h;
}

let cur = parse('');
let listeners = new Set();
let started = null;

export function current() { return cur; }
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function setLocation(hash, replace) {
  if (typeof history !== 'undefined' && replace) history.replaceState(null, '', hash);
  else if (typeof location !== 'undefined') location.hash = hash;
}

/** Go to a route. replace: no new history entry. silent: update the URL and current route but do
 *  not re-render (the view repaints itself). */
export function navigate(view, path = [], query = {}, opts = {}) {
  const hash = link(view, path, query);
  if (opts.silent) {
    cur = parse(hash);
    setLocation(hash, true);
    return;
  }
  if (typeof location !== 'undefined' && location.hash === hash) { if (started) started.handle(); return; }
  if (opts.replace) {
    setLocation(hash, true);
    if (started) started.handle();
  } else {
    setLocation(hash, false); // hashchange triggers handle()
  }
}

/** Say msg through the page's persistent polite live region (#pw-live in index.html), if any. */
export function announce(msg) {
  if (typeof document === 'undefined') return;
  const el = document.getElementById('pw-live');
  if (el) el.textContent = msg;
}

function sameTarget(a, b) {
  return a && b && a.view === b.view && a.path.join('/') === b.path.join('/');
}

function errorBox(root, title, detail, retry) {
  root.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'pw-error';
  box.setAttribute('role', 'alert');
  const h = document.createElement('h1');
  h.className = 'pw-error__title';
  h.tabIndex = -1;
  h.textContent = title;
  const p = document.createElement('p');
  p.textContent = detail;
  box.append(h, p);
  if (retry) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'pw-btn';
    b.textContent = 'Retry';
    b.addEventListener('click', retry);
    box.append(b);
  }
  root.append(box);
  return h;
}

/** Start routing. opts: {root, defaultView, normalize(route) -> route|null, beforeRender(route),
 *  afterRender(route, {targetChanged}), onTitle(title)}. */
export function start(routes, opts = {}) {
  const root = opts.root;
  const defaultView = opts.defaultView || 'teams';
  let token = 0;
  let cleanup = null;
  let shown = null;

  async function handle() {
    const my = ++token;
    let route = parse(location.hash);
    if (!route.view) { navigate(defaultView, [], route.query, { replace: true }); return; }
    if (opts.normalize) {
      const n = opts.normalize(route);
      if (n && link(n.view, n.path, n.query) !== link(route.view, route.path, route.query)) {
        history.replaceState(null, '', link(n.view, n.path, n.query));
        route = n;
      }
    }
    cur = route;
    for (const fn of Array.from(listeners)) { try { fn(route); } catch (e) { /* ignore */ } }
    const targetChanged = !sameTarget(shown, route);
    const activeId = document.activeElement && document.activeElement.id;
    if (typeof cleanup === 'function') { try { cleanup(); } catch (e) { /* ignore */ } }
    cleanup = null;
    const loader = routes[route.view];
    let h1 = null;
    const ctx = {
      root, route,
      isCurrent: () => my === token,
      setTitle: (t) => { if (my === token) { document.title = `${t} · PitWall`; if (opts.onTitle) opts.onTitle(t); } },
      loading: (msg) => {
        if (my !== token) return;
        root.innerHTML = '';
        const p = document.createElement('p');
        p.className = 'pw-loading';
        p.textContent = msg;
        root.append(p);
        announce(msg); // persistent live region: a status node inserted with its text is often not read
      },
      retry: () => handle(),
    };
    if (opts.beforeRender) opts.beforeRender(route);
    if (!loader) {
      document.title = 'Not found · PitWall';
      h1 = errorBox(root, 'No such page', `There is no "${route.view}" view. Use the navigation above.`, null);
    } else {
      try {
        if (targetChanged) ctx.loading('Loading');
        const mod = await loader();
        if (my !== token) return;
        const out = await mod.render(ctx);
        if (my !== token) { if (typeof out === 'function') out(); return; }
        cleanup = typeof out === 'function' ? out : null;
        h1 = root.querySelector('h1');
      } catch (err) {
        if (my !== token) return;
        console.error(err);
        const missing = err && /Failed to fetch dynamically imported|Cannot find module|Importing a module script failed|error loading dynamically/i.test(String(err.message));
        h1 = errorBox(root,
          missing ? 'This view is not available yet' : 'Could not load this view',
          missing ? 'The code for this tab is not part of this build.' : (err && err.message ? err.message : String(err)),
          missing ? null : () => handle());
        document.title = 'Error · PitWall';
      }
    }
    shown = route;
    announce('');
    if (opts.afterRender) opts.afterRender(route, { targetChanged });
    if (targetChanged) {
      if (h1) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); }
      if (typeof window !== 'undefined') window.scrollTo(0, 0);
    } else if (activeId) {
      const el = document.getElementById(activeId);
      if (el && el !== document.activeElement) el.focus({ preventScroll: true });
    }
  }

  started = { handle };
  window.addEventListener('hashchange', handle);
  handle();
  return { stop() { window.removeEventListener('hashchange', handle); started = null; }, handle };
}
