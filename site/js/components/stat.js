// Stat tile: value + unit + comparison (SPEC 5.5).
//   stat({label, value, delta: {text, dir: 'up'|'down'}, context, note, act: {label, key, expanded}})
import { html } from '../core/html.js';

export function stat(o) {
  const d = o.delta
    ? html`<span class="pw-delta pw-delta--${o.delta.dir === 'down' ? 'down' : 'up'}">${o.delta.dir === 'down' ? '▼' : '▲'} ${o.delta.text}</span>`
    : '';
  return html`<div class="pw-stat">
<span class="pw-eyebrow">${o.label}</span>
<div class="pw-stat__line"><span class="pw-stat__value">${o.value}</span>${d}</div>
<span class="pw-stat__context">${o.context}</span>
${o.note ? html`<span class="pw-small">${o.note}</span>` : ''}
${o.act ? html`<button type="button" class="pw-btn pw-stat__act" data-act="explain" data-key="${o.act.key}" aria-expanded="${o.act.expanded ? 'true' : 'false'}"${o.act.expanded ? html` aria-controls="pw-explain"` : ''}>Last 10 updates</button>` : ''}
</div>`;
}
