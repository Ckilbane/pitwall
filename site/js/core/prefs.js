// localStorage wrapper. Every access is in try/catch; failures fall back to memory for this page
// load (private mode, quota, disabled storage). Values are JSON.
const mem = new Map();

function store() {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch (e) { return null; }
}

export function read(key, fallback) {
  const s = store();
  if (s) {
    try {
      const raw = s.getItem(key);
      if (raw !== null) return JSON.parse(raw);
    } catch (e) { /* fall through */ }
  }
  return mem.has(key) ? mem.get(key) : fallback;
}

export function write(key, value) {
  mem.set(key, value);
  const s = store();
  if (s) {
    try {
      if (value === undefined || value === null) s.removeItem(key);
      else s.setItem(key, JSON.stringify(value));
    } catch (e) { /* memory only */ }
  }
}

export const KEYS = { theme: 'pw.v1.theme', prefs: 'pw.v1.prefs', watchlist: 'pw.v1.watchlist', offline: 'pw.v1.offline' };

/** Read pw.v1.prefs ({season, model, region}) as an object. */
export function readPrefs() {
  const p = read(KEYS.prefs, {});
  return p && typeof p === 'object' && !Array.isArray(p) ? p : {};
}

export function writePrefs(partial) {
  write(KEYS.prefs, { ...readPrefs(), ...partial });
}

export function _resetMemory() { mem.clear(); }
