// Number, date and label formatting (BRIEF voice: plain, units and comparisons, U+2212 minus).
export const MINUS = '−';
export const DASH = '–';

const num = (x) => typeof x === 'number' && Number.isFinite(x);

/** Total/auto/teleop/endgame EPA, points per match: "74.6". */
export function rating(x, nd = 1) { return num(x) ? x.toFixed(nd).replace(/^-/, MINUS) : DASH; }
/** RP-score EPA: "0.53". */
export function rp(x) { return num(x) ? x.toFixed(2).replace(/^-/, MINUS) : DASH; }
/** Probability 0-1: "92%". */
export function pct(p, nd = 0) { return num(p) ? `${(p * 100).toFixed(nd)}%` : DASH; }
/** Signed change: "+3.3" / "−3.3" / "0.0". */
export function delta(x, nd = 1) {
  if (!num(x)) return DASH;
  const s = Math.abs(x).toFixed(nd);
  if (Number(s) === 0) return s;
  return (x > 0 ? '+' : MINUS) + s;
}
/** Integer with thousands separators: "3,690". */
export function int(x) { return num(x) ? Math.round(x).toLocaleString('en-US') : DASH; }

export function ordinal(n) {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  return `${n}${({ 1: 'st', 2: 'nd', 3: 'rd' })[n % 10] || 'th'}`;
}
/** "6th of 139". */
export function rank(r, n) {
  if (!num(r)) return DASH;
  return num(n) ? `${ordinal(r)} of ${n}` : ordinal(r);
}

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
export function numberWord(n) { return n >= 0 && n < WORDS.length ? WORDS[n] : String(n); }
export function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }

/** Match label: Q32, EF3-1, QF2-1, SF2-1, F1. Finals use the match number. */
export function matchLabel(level, set, number) {
  switch (level) {
    case 'qm': return `Q${number}`;
    case 'ef': return `EF${set}-${number}`;
    case 'qf': return `QF${set}-${number}`;
    case 'sf': return `SF${set}-${number}`;
    case 'f': return `F${number}`;
    default: return `${String(level).toUpperCase()}${set}-${number}`;
  }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function toDate(v) {
  if (v instanceof Date) return v;
  if (typeof v === 'number') return new Date(v * 1000);
  return new Date(v);
}

/** Local date and time: "Nov 11, 2026, 23:34". Pass tz (IANA) in tests. */
export function dateTime(v, tz) {
  if (v === null || v === undefined || v === '') return DASH;
  const d = toDate(v);
  if (Number.isNaN(d.getTime())) return DASH;
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(d);
  const p = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return `${p.month} ${p.day}, ${p.year}, ${p.hour}:${p.minute}`;
}

/** "Mar 14" from YYYY-MM-DD (no timezone shift). */
export function dateShort(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
  return m ? `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}` : DASH;
}

export function record(w, l, t) {
  return [w, l, t].every((v) => v === null || v === undefined) ? DASH : `${w ?? 0}-${l ?? 0}-${t ?? 0}`;
}

/** "auto_coral_points" -> "Auto coral points"; "coral_rp" -> "Coral RP". */
export function compLabel(name) {
  if (!name) return '';
  const words = String(name).split('_').filter(Boolean).map((w) => (w === 'rp' ? 'RP' : w.toLowerCase()));
  if (!words.length) return '';
  if (words[0] !== 'RP') words[0] = words[0][0].toUpperCase() + words[0].slice(1);
  return words.join(' ');
}

/** Season-specific label for a vector key ("comp_0_epa") given season.keys. */
export function vectorLabel(vk, keys) {
  const fixed = { epa: 'Total EPA', auto_epa: 'Auto EPA', teleop_epa: 'Teleop EPA', endgame_epa: 'Endgame EPA', tiebreaker_epa: 'Tiebreaker EPA' };
  if (fixed[vk]) return fixed[vk];
  const bk = vk.replace(/_epa$/, '');
  const name = keys && keys.names ? keys.names[bk] : null;
  return name ? compLabel(name) : compLabel(bk);
}

/** Unit of a vector key: "points" | "count" | "RP score". */
export function unit(vk, keys) {
  const u = keys && keys.units ? keys.units[vk] : null;
  if (u === 'rp_score') return 'RP score';
  return u || 'points';
}

/** Short event name: "FMA District Springside Chestnut Hill Event" -> "Springside Chestnut Hill". */
export function eventShort(ev) {
  let n = String(ev.name || ev.key || '');
  if (ev.type === 'district_cmp') return `${(ev.district || '').toUpperCase() || 'District'} DCMP`;
  if (ev.type === 'champs_div' || ev.type === 'champs') {
    return `${n.replace(/\s+Division$/i, '')} (Champs)`;
  }
  n = n.replace(/^(FIRST\s+)?(Mid-Atlantic|Chesapeake|in\s+Michigan|Indiana|North Carolina|Texas|South Carolina)\s+/i, '')
    .replace(/^(FMA|FIM|FIT|FIN|FNC|FCH|PNW|NE|ONT|PCH|ISR)\s+/i, '')
    .replace(/^District\s+/i, '')
    .replace(/\s+(District\s+)?Event$/i, '')
    .replace(/\s+presented by .*$/i, '');
  return n.trim() || ev.name || ev.key;
}

/** Round and format for CSV so the file shows what the table shows. */
export function round(x, nd = 1) { return num(x) ? Number(x.toFixed(nd)) : null; }

export function median(arr) {
  const a = arr.filter(num).sort((x, y) => x - y);
  if (!a.length) return null;
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}
