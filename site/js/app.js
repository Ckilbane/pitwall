// PitWall entry point: load the manifest, build the shell, start the hash router.
import * as data from './core/data.js';
import * as router from './core/router.js';
import * as state from './core/state.js';
import { applyTheme } from './core/state.js';
import { html, mount } from './core/html.js';
import * as shell from './components/shell.js';
import { registerWorker, initIndicator } from './core/offline.js';

export const routes = {
  teams: () => import('./views/teams.js'),
  team: () => import('./views/team.js'),
  events: () => import('./views/events.js'),
  event: () => import('./views/event.js'),
  match: () => import('./views/match.js'),
  compare: () => import('./views/compare.js'),
  hypothetical: () => import('./views/hypothetical.js'),
  breakdown: () => import('./views/breakdown.js'),
  models: () => import('./views/models.js'),
  offline: () => import('./views/offline.js'),
};

const NEEDS_MODEL = new Set(['teams', 'team', 'event', 'match', 'compare', 'hypothetical', 'breakdown']);
const NEEDS_SEASON = new Set(['teams', 'team', 'events', 'compare', 'hypothetical', 'breakdown', 'models']);

/** Fill missing or invalid season/model in the URL from prefs then manifest defaults, so the address
 *  bar always shows the full state (SPEC 5.3). */
export function normalize(route) {
  const r = state.resolve(route.query);
  const q = { ...route.query };
  if (NEEDS_SEASON.has(route.view)) q.season = String(r.season);
  if (NEEDS_MODEL.has(route.view)) q.model = r.model;
  return { ...route, query: q };
}

function showBootError(err) {
  const main = document.getElementById('main');
  mount(main, html`<div class="pw-error" role="alert"><h1 class="pw-error__title" tabindex="-1">PitWall could not load its data</h1>
<p>${err && err.message ? err.message : String(err)}</p>
<button type="button" class="pw-btn" id="pw-retry">Retry</button></div>`);
  document.getElementById('pw-retry').addEventListener('click', () => boot());
  const h = main.querySelector('h1');
  if (h) h.focus();
}

let booted = false;
async function boot() {
  let manifest;
  try {
    manifest = await data.getManifest({ force: true });
  } catch (err) {
    shell.renderControls(null);
    shell.renderStamp(null);
    showBootError(err);
    return;
  }
  state.init(manifest);
  applyTheme(state.get().themePref);
  if (!booted) { booted = true; shell.bindShell(); state.watchSystemTheme(); }
  shell.renderControls(manifest);
  shell.renderStamp(manifest);
  shell.renderFooter(manifest);
  shell.renderNotices(manifest);
  router.subscribe((route) => shell.syncShell(route));
  router.start(routes, {
    root: document.getElementById('main'),
    normalize,
    afterRender: (route) => { shell.syncShell(route); shell.renderNotices(manifest); },
  });
}

// Offline support (WP6) is independent of the data: the indicator and the worker start even when the
// manifest cannot be read.
initIndicator();
if (document.readyState === 'complete') registerWorker();
else window.addEventListener('load', () => registerWorker(), { once: true });
boot();
