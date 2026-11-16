// Escaping tagged template. Every interpolated value is escaped unless it is SafeHTML (made by
// html`` or raw()). Arrays are joined; null/undefined/false render as nothing.

export class SafeHTML {
  constructor(s) { this.s = String(s); }
  toString() { return this.s; }
}

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(v) {
  return String(v).replace(/[&<>"']/g, (c) => ESC[c]);
}

function part(v) {
  if (v === null || v === undefined || v === false) return '';
  if (v instanceof SafeHTML) return v.s;
  if (Array.isArray(v)) return v.map(part).join('');
  return esc(v);
}

export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += part(vals[i]) + strings[i + 1];
  return new SafeHTML(out);
}

/** Mark a string as already-safe markup (use only for strings built by html`` or trusted constants). */
export function raw(s) { return new SafeHTML(s); }

export function mount(el, safe) {
  el.innerHTML = safe instanceof SafeHTML ? safe.s : esc(safe);
  return el;
}
