// Watchlist (WP5). Per-device list of team numbers, kept in localStorage under pw.v1.watchlist.
// Every storage access is in try/catch and falls back to memory (private mode, quota).
//
//   get() -> number[]          current list (copy). Defaults to [1218, 1219] until the manifest
//                              default is supplied with setDefault().
//   has(team) -> boolean
//   toggle(team) -> boolean    true when the team is now watched
//   add(team), remove(team)
//   set(list)                  replace the list
//   setDefault(list)           the manifest's default_watchlist; used only while the user has
//                              never saved a list on this device
//   subscribe(fn) -> unsubscribe   fn(list) runs after every change (also from other tabs)

export const KEY = 'pw.v1.watchlist';
const FALLBACK = [1218, 1219];

let defaults = FALLBACK.slice();
let memory = null; // list kept in memory when storage is unavailable, or after a write
const subs = new Set();

function clean(list) {
  const out = [];
  if (!Array.isArray(list)) return out;
  for (const v of list) {
    const n = Number(v);
    if (Number.isInteger(n) && n > 0 && n < 100000 && !out.includes(n)) out.push(n);
  }
  return out;
}

function store() {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch (e) {
    return null;
  }
}

function load() {
  const s = store();
  if (s) {
    try {
      const raw = s.getItem(KEY);
      if (raw !== null) {
        const list = clean(JSON.parse(raw));
        memory = list;
        return list;
      }
    } catch (e) { /* fall through to memory */ }
  }
  return memory; // null when the user never saved a list
}

function save(list) {
  memory = list;
  const s = store();
  if (s) {
    try { s.setItem(KEY, JSON.stringify(list)); } catch (e) { /* memory only */ }
  }
  for (const fn of Array.from(subs)) {
    try { fn(list.slice()); } catch (e) { /* a bad subscriber must not break the others */ }
  }
}

export function get() {
  const saved = load();
  return (saved !== null ? saved : defaults).slice();
}

export function has(team) {
  return get().includes(Number(team));
}

export function set(list) {
  save(clean(list));
}

export function add(team) {
  const n = Number(team);
  const list = get();
  if (Number.isInteger(n) && n > 0 && !list.includes(n)) save(list.concat(n));
}

export function remove(team) {
  const n = Number(team);
  const list = get();
  if (list.includes(n)) save(list.filter((t) => t !== n));
}

export function toggle(team) {
  const n = Number(team);
  if (!Number.isInteger(n) || n <= 0) return false;
  if (has(n)) { remove(n); return false; }
  add(n);
  return true;
}

export function setDefault(list) {
  const c = clean(list);
  if (c.length) defaults = c;
}

export function subscribe(fn) {
  subs.add(fn);
  return () => subs.delete(fn);
}

// Changes made in another tab.
if (typeof window !== 'undefined' && window.addEventListener) {
  window.addEventListener('storage', (e) => {
    if (e.key !== KEY) return;
    memory = null;
    const list = get();
    for (const fn of Array.from(subs)) {
      try { fn(list.slice()); } catch (err) { /* ignore */ }
    }
  });
}

/** Test hook: forget memory and subscribers, restore the built-in default. */
export function _reset() {
  memory = null;
  subs.clear();
  defaults = FALLBACK.slice();
}
