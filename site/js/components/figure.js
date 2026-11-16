// Figure (SPEC 5.5): finding-as-title, subtitle with scope, caption with encodings, n, source, update.
import { html, raw } from '../core/html.js';

/** figure({id, title, sub, body, data, caption, cls}) (data: a table for the Show data toggle) -> SafeHTML. body/caption may be SafeHTML. */
export function figure(o) {
  const id = o.id || null;
  return html`<figure class="pw-figure ${o.cls || ''}"${id ? html` aria-labelledby="${id}-t"` : ''}>
<h2 class="pw-figure__title"${id ? html` id="${id}-t"` : ''}>${o.title}</h2>
${o.sub ? html`<p class="pw-figure__sub">${o.sub}</p>` : ''}
${o.body}
${o.data ? showData(o.data) : ''}
${o.caption ? html`<figcaption class="pw-caption">${o.caption}</figcaption>` : ''}
</figure>`;
}

/** "Show data" toggle holding the same numbers as the chart (a dataTable() result or a trusted html string). */
export function showData(table, label = 'Show data') {
  return html`<details class="pw-data"><summary>${label}</summary><div class="pw-data__box">${typeof table === 'string' ? raw(table) : table}</div></details>`;
}

/** Render `draw(width)` into host and redraw when the width changes meaningfully. Returns a stop fn. */
export function fitChart(host, draw) {
  let last = 0;
  const run = () => {
    const w = Math.round(host.clientWidth);
    if (!w || Math.abs(w - last) < 24) return;
    last = w;
    host.innerHTML = draw(w);
  };
  run();
  if (typeof ResizeObserver === 'undefined') return () => {};
  const ro = new ResizeObserver(run);
  ro.observe(host);
  return () => ro.disconnect();
}
