// App shell: header controls (find, season, model, theme), nav state, data-updated stamp, notices,
// footer credits. The static frame (lockup, nav, footer) is in index.html; this fills in the rest.
import { html, mount, esc } from '../core/html.js';
import * as router from '../core/router.js';
import * as state from '../core/state.js';
import * as prefs from '../core/prefs.js';
import { dateTime } from '../core/fmt.js';
import { getSeason, getManifest, isAnyStale, onStale } from '../core/data.js';
import { seg } from './seg.js';

const $ = (id) => document.getElementById(id);

/** Find results for a query against a decoded season: teams by number or name, events by key or name. */
export function findResults(season, q, limit = 8) {
  const query = String(q || '').trim().toLowerCase();
  if (!query || !season) return [];
  const sn = season.season;
  const tc = season.teams.cols;
  const ec = season.events.cols;
  const numeric = /^\d+$/.test(query);
  const teams = [];
  for (let i = 0; i < season.teams.n; i++) {
    const num = String(tc.team[i]);
    const name = String(tc.name[i] || '');
    let score = -1;
    if (num === query) score = 0;
    else if (numeric && num.startsWith(query)) score = 1;
    else if (!numeric && name.toLowerCase().startsWith(query)) score = 2;
    else if (!numeric && name.toLowerCase().includes(query)) score = 3;
    if (score >= 0) teams.push({ score, n: tc.team[i], name });
  }
  teams.sort((a, b) => a.score - b.score || a.n - b.n);
  const events = [];
  for (let i = 0; i < season.events.n; i++) {
    const key = ec.key[i];
    const name = String(ec.name[i] || '');
    const hay = `${key} ${name}`.toLowerCase();
    if (hay.includes(query)) events.push({ key, name, wk: ec.week[i] });
  }
  const nTeams = Math.min(teams.length, events.length ? Math.max(limit - 3, 3) : limit);
  const out = teams.slice(0, nTeams).map((t) => ({
    kind: 'Team', id: `team-${t.n}`, num: String(t.n), label: t.name, href: router.link('team', [t.n], { season: sn }),
  }));
  for (const e of events.slice(0, limit - out.length)) {
    out.push({ kind: 'Event', id: `event-${e.key}`, num: e.key, label: e.name, href: router.link('event', [e.key]) });
  }
  return out;
}

export function renderControls(manifest) {
  const el = $('pw-controls');
  if (!el) return;
  const st = manifest ? state.get() : null;
  const seasons = manifest ? manifest.seasons : [];
  const models = manifest ? manifest.models : [];
  mount(el, html`
${manifest ? html`<div class="pw-field pw-find"><label class="pw-eyebrow" for="pw-find">Find</label>
<input class="pw-input" id="pw-find" type="search" placeholder="Team or event" autocomplete="off" role="combobox" aria-expanded="false" aria-controls="pw-find-list" aria-autocomplete="list" aria-describedby="pw-find-hint">
<span id="pw-find-hint" class="pw-sr">Type a team number, team name or event name, then use the arrow keys and Enter.</span>
<ul class="pw-find__list" id="pw-find-list" role="listbox" aria-label="Find results" hidden></ul></div>
<label class="pw-field"><span class="pw-eyebrow">Season</span>
<select class="pw-select" id="pw-season">${seasons.map((s) => html`<option value="${s.season}"${st && st.season === s.season ? html` selected` : ''}>${s.season}${s.status === 'live' ? ' (live)' : ''}</option>`)}</select></label>
${seg({ label: 'Rating model', act: 'model', value: st.model, options: models.map((m) => ({ id: m.id, label: m.label })) })}` : ''}
<div id="pw-theme-seg">${themeSeg()}</div>`);
}

function themeSeg() {
  const t = state.get().theme;
  return seg({ label: 'Theme', act: 'theme', value: t, options: [{ id: 'light', label: 'Light' }, { id: 'dark', label: 'Dark' }] });
}

const NAV = ['teams', 'team', 'events', 'event', 'match', 'compare', 'hypothetical', 'breakdown', 'models'];

function lastViewed() { return prefs.read('pw.v1.last', {}) || {}; }

/** Nav hrefs and aria-current for the route; control values from state. */
export function syncShell(route) {
  const nav = $('pw-nav');
  if (nav) {
    const last = lastViewed();
    const wl = state.get().watchlist;
    const st = state.get();
    const q = { season: st.season, model: st.model };
    const hrefs = {
      teams: router.link('teams', [], q),
      team: router.link('team', [last.team || wl[0] || 1218], q),
      events: router.link('events', [], { season: st.season }),
      event: last.event ? router.link('event', [last.event], { model: st.model }) : router.link('events', [], { season: st.season }),
      match: last.match ? router.link('match', [last.match], { model: st.model }) : router.link('events', [], { season: st.season }),
      compare: router.link('compare', [], q),
      hypothetical: router.link('hypothetical', [], q),
      breakdown: router.link('breakdown', [], q),
      models: router.link('models', [], { season: st.season }),
    };
    for (const a of nav.querySelectorAll('a[data-nav]')) {
      const v = a.getAttribute('data-nav');
      if (hrefs[v]) a.setAttribute('href', hrefs[v]);
      if (route && route.view === v) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
  }
  const st = state.get();
  const sel = $('pw-season');
  if (sel && String(sel.value) !== String(st.season)) sel.value = String(st.season);
  for (const b of document.querySelectorAll('[data-act="model"]')) b.setAttribute('aria-pressed', String(b.dataset.value === st.model));
  for (const b of document.querySelectorAll('[data-act="theme"]')) b.setAttribute('aria-pressed', String(b.dataset.value === st.theme));
  if (route) {
    if (route.view === 'team' && route.path[0]) prefs.write('pw.v1.last', { ...lastViewed(), team: Number(route.path[0]) });
    if (route.view === 'event' && route.path[0]) prefs.write('pw.v1.last', { ...lastViewed(), event: route.path[0] });
    if (route.view === 'match' && route.path[0]) prefs.write('pw.v1.last', { ...lastViewed(), match: route.path[0] });
  }
}

export function renderStamp(manifest) {
  const el = $('pw-updated');
  if (!el) return;
  el.textContent = manifest
    ? `FRC ratings from The Blue Alliance · data updated ${dateTime(manifest.generated_at)}`
    : 'FRC ratings from The Blue Alliance';
}

export function renderNotices(manifest) {
  const el = $('pw-notices');
  if (!el) return;
  const items = [];
  if (manifest) {
    for (const s of manifest.seasons) {
      if (s.stale) items.push(`${s.season} data is from an earlier build${s.stale_reason ? ` (${s.stale_reason})` : ''}. The latest update for that season failed.`);
    }
  }
  if (isAnyStale()) items.push(`Showing saved data${manifest ? ` from ${dateTime(manifest.generated_at)}` : ''}; you are offline or the site could not reach new data.`);
  mount(el, html`${items.map((t) => html`<div class="pw-notice" role="status">${t}</div>`)}`);
}

export function renderFooter(manifest) {
  const el = $('pw-credits');
  if (!el || !manifest || !manifest.credits) return;
  const c = manifest.credits;
  el.textContent = `PitWall · built by FRC 1218 · match data from ${c.data || 'The Blue Alliance'} · EPA model from ${(c.model || 'Statbotics (MIT license)').replace(/^Statbotics EPA \(MIT\)$/, 'Statbotics (MIT license)')}`;
  const n = $('pw-disclaimer');
  if (n && c.notice) n.textContent = c.notice.replace(/\.$/, '');
}

// ---- find combobox ----
let findSeason = null;
let findActive = -1;
let findItems = [];

async function ensureFindData() {
  const st = state.get();
  if (findSeason && findSeason.season === st.season) return findSeason;
  findSeason = await getSeason(st.season);
  return findSeason;
}

function closeFind() {
  const input = $('pw-find');
  const list = $('pw-find-list');
  if (!input || !list) return;
  list.hidden = true;
  input.setAttribute('aria-expanded', 'false');
  input.removeAttribute('aria-activedescendant');
  findActive = -1;
}

function setActive(i) {
  const list = $('pw-find-list');
  const input = $('pw-find');
  findActive = i;
  list.querySelectorAll('[role="option"]').forEach((o, k) => o.setAttribute('aria-selected', String(k === i)));
  if (i >= 0 && list.children[i]) input.setAttribute('aria-activedescendant', list.children[i].id);
  else input.removeAttribute('aria-activedescendant');
}

async function updateFind() {
  const input = $('pw-find');
  const list = $('pw-find-list');
  if (!input || !list) return;
  const q = input.value;
  if (!q.trim()) { closeFind(); return; }
  let season;
  try { season = await ensureFindData(); } catch (e) { return; }
  if (input.value !== q) return;
  findItems = findResults(season, q);
  list.innerHTML = findItems.length
    ? findItems.map((r, i) => `<li role="option" id="pw-find-${i}" aria-selected="false"><a class="pw-find__item" tabindex="-1" href="${esc(r.href)}"><span class="pw-find__kind">${r.kind}</span><span class="pw-find__num">${esc(r.num)}</span><span>${esc(r.label)}</span></a></li>`).join('')
    : '<li class="pw-find__empty" role="presentation">No team or event matches.</li>';
  list.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  setActive(-1);
}

function onFindKey(e) {
  const list = $('pw-find-list');
  if (e.key === 'ArrowDown' && findItems.length) { e.preventDefault(); if (list.hidden) updateFind(); else setActive((findActive + 1) % findItems.length); }
  else if (e.key === 'ArrowUp' && findItems.length) { e.preventDefault(); setActive((findActive - 1 + findItems.length) % findItems.length); }
  else if (e.key === 'Enter') {
    const pick = findItems[findActive >= 0 ? findActive : 0];
    if (pick && !list.hidden) { e.preventDefault(); location.hash = pick.href; $('pw-find').value = ''; closeFind(); }
  } else if (e.key === 'Escape') { if (!list.hidden) { e.preventDefault(); e.stopPropagation(); closeFind(); } else e.target.value = ''; }
}

/** Wire all shell events once (delegation on document). */
export function bindShell() {
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-act]');
    if (t) {
      const act = t.dataset.act;
      if (act === 'theme') { state.setTheme(t.dataset.value); syncShell(router.current()); }
      else if (act === 'model') state.setQuery({ model: t.dataset.value }, { replace: true });
      else if (act === 'controls') {
        const top = t.closest('.pw-top');
        const open = top.dataset.open !== 'true';
        top.dataset.open = String(open);
        t.setAttribute('aria-expanded', String(open));
      } else if (act === 'skip') { e.preventDefault(); const m = $('main'); if (m) m.focus(); }
    }
    const f = e.target.closest('.pw-find__item');
    if (f) { const i = $('pw-find'); if (i) i.value = ''; closeFind(); const top = document.querySelector('.pw-top'); if (top) { top.dataset.open = 'false'; } }
    if (!e.target.closest('.pw-find')) closeFind();
  });
  document.addEventListener('change', (e) => {
    if (e.target.id === 'pw-season') {
      const season = e.target.value;
      const r = router.current();
      if (r.view === 'event' || r.view === 'match') { prefs.writePrefs({ season: Number(season) }); router.navigate('events', [], { season }); }
      else state.setQuery({ season }, { replace: false });
    }
  });
  document.addEventListener('input', (e) => { if (e.target.id === 'pw-find') updateFind(); });
  document.addEventListener('keydown', (e) => { if (e.target.id === 'pw-find') onFindKey(e); });
  document.addEventListener('focusin', (e) => { if (e.target.id === 'pw-find') ensureFindData().catch(() => {}); });
  document.addEventListener('mousedown', (e) => { if (e.target.closest('.pw-find__list')) e.preventDefault(); });
  onStale(() => getManifest().then(renderNotices).catch(() => {}));
}

export { NAV };
