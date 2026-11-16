// Segmented control: pw-seg with aria-pressed buttons (SPEC 5.5).
//   seg({label, options: [{id, label, disabled}], value, act, attrs})
import { html } from '../core/html.js';

export function seg(o) {
  return html`<div class="pw-seg" role="group" aria-label="${o.label}">${o.options.map((opt) => html`<button type="button" data-act="${o.act}" data-value="${opt.id}" aria-pressed="${opt.id === o.value ? 'true' : 'false'}"${opt.disabled ? html` disabled` : ''}>${opt.label}</button>`)}</div>`;
}
