// Data table (SPEC 5.5): sortable button headers with aria-sort, numeric right-aligned mono,
// optional sticky leading columns, a scroll box that is keyboard reachable.
//
//   dataTable({columns, rows, sort: {col, dir: 1|-1}, rowClass(row), rowAttrs(row), label, labelledby})
//     column: {id, label, n, td: 'team', sortable, sticky: 'a'|'b', render(row, i) -> string|SafeHTML, title}
//     groupRow(row, i) -> null | string|SafeHTML: a full-width header row (scope=colgroup) before this row
//   sortRows(rows, getter, dir) -> new array, nulls last, stable
import { html, raw, esc, SafeHTML } from '../core/html.js';

export function sortRows(rows, getter, dir = 1) {
  const a = rows.map((r, i) => ({ r, i, v: getter(r) }));
  a.sort((x, y) => {
    const xn = x.v === null || x.v === undefined || (typeof x.v === 'number' && Number.isNaN(x.v));
    const yn = y.v === null || y.v === undefined || (typeof y.v === 'number' && Number.isNaN(y.v));
    if (xn || yn) return xn && yn ? x.i - y.i : xn ? 1 : -1;
    let c;
    if (typeof x.v === 'number' && typeof y.v === 'number') c = x.v - y.v;
    else c = String(x.v).localeCompare(String(y.v), 'en', { numeric: true, sensitivity: 'base' });
    return c ? c * dir : x.i - y.i;
  });
  return a.map((e) => e.r);
}

function cellHtml(v) { return v instanceof SafeHTML ? v.s : esc(v ?? ''); }

export function dataTable(o) {
  const { columns, rows } = o;
  const sort = o.sort || {};
  const head = columns.map((c) => {
    const cls = [c.n ? 'n' : '', c.sticky ? `pw-sticky-${c.sticky}` : '', c.thClass || '', c.cls || ''].filter(Boolean).join(' ');
    const active = sort.col === c.id;
    const ariaSort = c.sortable ? ` aria-sort="${active ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}"` : '';
    const inner = c.sortable
      ? `<button type="button" class="pw-sort" data-act="sort" data-col="${esc(c.id)}">${esc(c.label)}${active ? `<span aria-hidden="true">${sort.dir > 0 ? '▲' : '▼'}</span>` : ''}</button>`
      : esc(c.label);
    return `<th scope="col"${cls ? ` class="${cls}"` : ''}${ariaSort}>${inner}${c.headExtra ? cellHtml(c.headExtra) : ''}</th>`;
  }).join('');
  const body = rows.map((row, i) => {
    const rc = o.rowClass ? o.rowClass(row) : '';
    const ra = o.rowAttrs ? o.rowAttrs(row) : '';
    const tds = columns.map((c) => {
      const cls = [c.n ? 'n' : '', c.td || '', c.sticky ? `pw-sticky-${c.sticky}` : '', c.cls || ''].filter(Boolean).join(' ');
      return `<td${cls ? ` class="${cls}"` : ''}>${cellHtml(c.render(row, i))}</td>`;
    }).join('');
    const g = o.groupRow ? o.groupRow(row, i) : null;
    const gh = g ? `<tr class="pw-tier"><th colspan="${columns.length}" scope="colgroup">${cellHtml(g)}</th></tr>` : '';
    return `${gh}<tr${rc ? ` class="${rc}"` : ''}${ra ? ` ${ra}` : ''}>${tds}</tr>`;
  }).join('');
  const aria = o.labelledby ? `aria-labelledby="${esc(o.labelledby)}"` : `aria-label="${esc(o.label || 'Table')}"`;
  return raw(`<div class="pw-scroll" tabindex="0" role="region" ${aria}><table class="pw-table" ${aria}><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
}
