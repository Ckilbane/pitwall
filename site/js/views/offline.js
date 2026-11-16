// Offline (WP6, SPEC 5.6/6): per-season size, what is saved on this device, download with progress,
// remove, storage used. Route: #/offline
import { html, mount } from '../core/html.js';
import { getManifest } from '../core/data.js';
import { announce } from '../core/router.js';
import { dateTime, plural } from '../core/fmt.js';
import * as off from '../core/offline.js';
import { pageHead } from '../components/page.js';
import { dataTable } from '../components/table.js';

/** Status words for one season row (exported for tests). */
export function statusText(s, j) {
  if (j && !j.finished) return j.total ? `Downloading: ${j.done} of ${j.total} files` : 'Starting download';
  if (j && j.error) return `Download stopped: ${j.error.message || j.error}`;
  if (s.total && s.have >= s.total) return 'Saved, up to date';
  if (s.have > 0 && s.recorded) return `Partly saved: ${s.have} of ${s.total} files. Newer data was published since the download`;
  if (s.have > 0) return `Partly saved from pages you viewed: ${s.have} of ${s.total} files`;
  if (s.older > 0) return `An older copy is saved (${plural(s.older, 'file')}). Download to update`;
  return 'Not saved';
}

/** Progress line: "54 of 206 files, 4.1 of 21.0 MB". */
export function progressText(season, j) {
  if (!j || !j.total) return `Preparing ${season}`;
  return `${season}: ${j.done} of ${j.total} files, ${off.mb(j.bytesDone)} of ${off.mb(j.bytesTotal)}`;
}

/** The lede: how many seasons are fully saved, and their size. */
export function ledeText(rows, sup) {
  if (!sup.caches) return 'This browser cannot save PitWall for offline use, so it needs a connection. Pages still work normally online.';
  const full = rows.filter((r) => r.total && r.have >= r.total);
  const bytes = full.reduce((a, r) => a + r.totalBytes, 0);
  if (!full.length) return 'No season is saved for offline use yet. Pages you view are kept as you go; download a season to have every team, event and match without a connection.';
  return `${full.length === rows.length ? 'Every season is' : `${plural(full.length, 'season')} of ${rows.length} ${full.length === 1 ? 'is' : 'are'}`} saved on this device (${off.mb(bytes)}): ${full.map((r) => r.season).join(', ')}. Every view of a saved season works without a connection.`;
}

export async function render(ctx) {
  const { root } = ctx;
  ctx.setTitle('Offline');
  ctx.loading('Checking what is saved on this device');
  const manifest = await getManifest();
  let rows = await Promise.all(manifest.seasons.map((s) => off.seasonStatus(manifest, s.season)));
  let store = await off.storage();
  if (!ctx.isCurrent()) return undefined;
  const sup = off.support();
  let lastSpoken = 0;

  function actions(s) {
    const j = off.job(s.season);
    const busy = j && !j.finished;
    const complete = s.total && s.have >= s.total;
    const parts = [];
    if (busy) parts.push(html`<button type="button" class="pw-btn" id="pw-off-cancel-${s.season}" data-act="off-cancel" data-season="${s.season}">Cancel<span class="pw-sr"> ${s.season} download</span></button>`);
    else if (!complete && sup.caches) parts.push(html`<button type="button" class="pw-btn" id="pw-off-get-${s.season}" data-act="off-get" data-season="${s.season}">${s.recorded || s.older ? 'Update' : 'Download'}<span class="pw-sr"> ${s.season} season, ${off.mb(s.totalBytes)}</span></button>`);
    if (!busy && (s.have > 0 || s.older > 0)) parts.push(html`<button type="button" class="pw-btn" id="pw-off-rm-${s.season}" data-act="off-rm" data-season="${s.season}">Remove<span class="pw-sr"> saved ${s.season} data</span></button>`);
    return html`<span class="pw-off-actions">${parts}</span>`;
  }

  function progressBlock() {
    const active = rows.map((r) => [r.season, off.job(r.season)]).filter(([, j]) => j && !j.finished);
    if (!active.length) return '';
    return html`${active.map(([y, j]) => html`<div class="pw-off-progress">
<span class="pw-eyebrow" id="pw-off-plabel-${y}">Downloading ${y}</span>
<progress id="pw-off-bar-${y}" max="${j.bytesTotal || 1}" value="${j.bytesDone}" aria-labelledby="pw-off-plabel-${y}" aria-describedby="pw-off-ptext-${y}"></progress>
<span class="pw-small" id="pw-off-ptext-${y}">${progressText(y, j)}</span></div>`)}`;
  }

  function storageLine() {
    const parts = [];
    if (typeof store.usage === 'number') parts.push(`PitWall uses ${off.mb(store.usage)} on this device${typeof store.quota === 'number' ? ` of about ${Math.round(store.quota / 1e6).toLocaleString('en-US')} MB the browser allows it` : ''}.`);
    if (store.persisted === true) parts.push('Storage is marked persistent, so the browser will not clear it on its own.');
    else if (store.persisted === false) parts.push('The browser may clear saved data when the device runs low on space.');
    return parts.join(' ');
  }

  function paint(focusId) {
    const table = dataTable({
      label: 'Seasons saved for offline use',
      columns: [
        { id: 'season', label: 'Season', render: (s) => String(s.season), td: 'team', sticky: 'a' },
        { id: 'game', label: 'Game', render: (s) => s.game || '' },
        { id: 'size', label: 'Size', n: true, render: (s) => off.mb(s.totalBytes) },
        { id: 'files', label: 'Files', n: true, render: (s) => String(s.total) },
        { id: 'saved', label: 'Saved', n: true, render: (s) => off.mb(s.haveBytes) },
        { id: 'status', label: 'Status', render: (s) => statusText(s, off.job(s.season)), td: 'wrap' },
        { id: 'act', label: 'Actions', render: (s) => actions(s) },
      ],
      rows,
    });
    mount(root, html`${pageHead({ eyebrow: 'Offline', title: 'Use PitWall without a connection', lede: ledeText(rows, sup) })}
<section class="pw-section" aria-labelledby="pw-off-seasons">
<h2 class="pw-h2" id="pw-off-seasons">Seasons</h2>
<div id="pw-off-progress" class="pw-off-progresses">${progressBlock()}</div>
${table}
<p class="pw-small">Sizes are compressed download sizes. Data published ${dateTime(manifest.generated_at)}; a season saved before that is updated by downloading it again (only changed files are fetched).</p>
</section>
<section class="pw-section pw-prose" aria-labelledby="pw-off-how">
<h2 class="pw-h2" id="pw-off-how">How offline works</h2>
<p>The app itself is saved on your first visit. After that, every page you open is kept on this device, so it opens again without a connection. Downloading a season saves all of its teams, events and matches at once (about 10 to 20 MB). When you are offline the header says so, and pages show the newest data saved here.</p>
${!sup.worker ? html`<p>This browser does not support service workers, so saved data cannot be used offline.</p>` : !sup.controlled ? html`<p>Offline use starts after this page has loaded once with the service worker running. Reload once if you just opened PitWall for the first time.</p>` : ''}
<p class="pw-small" id="pw-off-storage">${storageLine()}</p>
</section>`);
    if (focusId) { const el = document.getElementById(focusId); if (el) el.focus(); }
  }

  async function refresh(focusId) {
    const m = await getManifest();
    rows = await Promise.all(m.seasons.map((s) => off.seasonStatus(m, s.season)));
    store = await off.storage();
    if (!ctx.isCurrent()) return;
    paint(focusId);
  }

  function say(text) {
    announce(text);
  }

  // progress: update bars and text in place (focus stays where it is); speak at most every 4 s
  const unsub = off.onJob((season, j) => {
    if (!ctx.isCurrent()) return;
    if (!j || j.finished) {
      const focusWas = document.activeElement && document.activeElement.id;
      const target = focusWas && /^pw-off-(cancel|get)-/.test(focusWas) ? `pw-off-rm-${season}` : focusWas;
      refresh(target).then(() => {
        if (!target || !document.getElementById(target)) { const b = document.getElementById(`pw-off-get-${season}`); if (b && focusWas) b.focus(); }
        if (j && j.error) say(`Download of ${season} stopped: ${j.error.message || j.error}`);
        else if (j && j.cancelled) say(`Download of ${season} cancelled.`);
        else if (j) say(`${season} is saved for offline use.`);
      });
      return;
    }
    const wrap = document.getElementById('pw-off-progress');
    const bar = document.getElementById(`pw-off-bar-${season}`);
    if (!bar && wrap) { mount(wrap, progressBlock()); }
    const b = document.getElementById(`pw-off-bar-${season}`);
    if (b) { b.max = j.bytesTotal || 1; b.value = j.bytesDone; }
    const t = document.getElementById(`pw-off-ptext-${season}`);
    if (t) t.textContent = progressText(season, j);
    const now = Date.now();
    if (now - lastSpoken > 4000) { lastSpoken = now; say(progressText(season, j)); }
  });

  function onClick(e) {
    const b = e.target.closest('[data-act^="off-"]');
    if (!b) return;
    const season = Number(b.dataset.season);
    const act = b.dataset.act;
    if (act === 'off-get') {
      off.download(season);
      paint(`pw-off-cancel-${season}`);
      say(`Downloading ${season}.`);
    } else if (act === 'off-cancel') {
      off.cancel(season);
    } else if (act === 'off-rm') {
      off.remove(season).then((n) => refresh(`pw-off-get-${season}`).then(() => say(`Removed ${plural(n, 'saved file')} for ${season}.`)));
    }
  }
  root.addEventListener('click', onClick);
  paint();
  return () => { unsub(); root.removeEventListener('click', onClick); };
}
