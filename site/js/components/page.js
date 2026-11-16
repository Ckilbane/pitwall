// Page head shared by every view: eyebrow, h1, the lede (the decision sentence, first thing on the page).
//   pageHead({eyebrow, title, lede, ledeId, after}) -> SafeHTML
//   field({id, label, control}) -> SafeHTML   labelled control in the filters row
//   note(text, {live}) -> SafeHTML            small status line (polite live region when live)
import { html } from '../core/html.js';

export function pageHead(o) {
  return html`<section class="pw-head">
<span class="pw-eyebrow">${o.eyebrow}</span>
<h1 class="pw-h1" tabindex="-1">${o.title}</h1>
${o.after || ''}
${o.lede === undefined || o.lede === null ? '' : html`<p class="pw-lede"${o.ledeId ? html` id="${o.ledeId}"` : ''}>${o.lede}</p>`}
</section>`;
}

export function field(o) {
  return html`<label class="pw-field"${o.id ? html` for="${o.id}"` : ''}><span class="pw-eyebrow">${o.label}</span>${o.control}</label>`;
}

export function note(text, o = {}) {
  return html`<p class="pw-small pw-note-line"${o.live ? html` role="status" aria-live="polite"` : ''}>${text}</p>`;
}
