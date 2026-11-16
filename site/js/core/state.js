// App state resolved as URL > localStorage prefs > manifest defaults.
//
//   init(manifest)            once, after the manifest loads
//   get() -> {season, model, theme, themePref, watchlist, manifest}
//   resolve(query) -> {season, model}     fill missing/invalid season and model for a query
//   setQuery(partial, {replace, silent}?) merge into the current hash query (null/'' removes a key)
//   subscribe(fn) -> unsubscribe          fn(state) on route, theme or watchlist changes
//   setTheme('light'|'dark'|null)         null = follow the system
//   getRegion(query, fallback)            region from URL, else prefs, else fallback
import * as router from './router.js';
import * as prefs from './prefs.js';
import { get as wlGet, subscribe as wlSub, setDefault as wlDefault } from './watchlist.js';

let manifest = null;
const subs = new Set();
let themePref = null;

function systemDark() {
  try { return typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { return false; }
}

function emit() {
  const s = get();
  for (const fn of Array.from(subs)) { try { fn(s); } catch (e) { console.error(e); } }
}

export function init(m) {
  manifest = m;
  try { wlDefault(m.default_watchlist || [1218, 1219]); } catch (e) { /* ignore */ }
  const t = prefs.read(prefs.KEYS.theme, null);
  themePref = t === 'light' || t === 'dark' ? t : null;
  router.subscribe(emit);
  wlSub(emit);
}

function seasonIds() { return manifest ? manifest.seasons.map((s) => s.season) : []; }

export function resolveSeason(q) {
  const ids = seasonIds();
  const want = [Number(q && q.season), Number(prefs.readPrefs().season), manifest && manifest.default_season];
  for (const w of want) if (ids.includes(w)) return w;
  return ids[0];
}

export function resolveModel(q) {
  const ids = manifest ? manifest.models.map((m) => m.id) : ['epa'];
  const want = [q && q.model, prefs.readPrefs().model, manifest && manifest.default_model];
  for (const w of want) if (ids.includes(w)) return w;
  return ids[0];
}

export function resolve(query) { return { season: resolveSeason(query), model: resolveModel(query) }; }

export function getRegion(query, fallback) {
  return (query && query.region) || prefs.readPrefs().region || fallback || (manifest && manifest.default_region) || 'all';
}

export function get() {
  const q = router.current().query;
  return {
    ...resolve(q),
    theme: themePref || (systemDark() ? 'dark' : 'light'),
    themePref,
    watchlist: wlGet(),
    manifest,
  };
}

export function setQuery(partial, opts = {}) {
  const r = router.current();
  const q = { ...r.query };
  for (const [k, v] of Object.entries(partial)) {
    if (v === null || v === undefined || v === '') delete q[k]; else q[k] = String(v);
  }
  // Remember the sticky choices for next visit.
  const keep = {};
  for (const k of ['season', 'model', 'region']) if (k in partial && partial[k]) keep[k] = String(partial[k]);
  if (Object.keys(keep).length) prefs.writePrefs(keep);
  router.navigate(r.view, r.path, q, opts);
}

export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }

export function applyTheme(t) {
  const root = document.documentElement;
  if (t === 'light' || t === 'dark') root.setAttribute('data-theme', t); else root.removeAttribute('data-theme');
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    const paper = getComputedStyle(root).getPropertyValue('--paper').trim();
    if (paper) meta.setAttribute('content', paper);
  }
}

export function setTheme(t) {
  themePref = t === 'light' || t === 'dark' ? t : null;
  prefs.write(prefs.KEYS.theme, themePref);
  applyTheme(themePref);
  emit();
}

export function watchSystemTheme() {
  try {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const fn = () => { if (!themePref) { applyTheme(null); emit(); } };
    mq.addEventListener ? mq.addEventListener('change', fn) : mq.addListener(fn);
  } catch (e) { /* ignore */ }
}
