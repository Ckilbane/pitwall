// Explain-this-number: a rating's last 10 updates (match, predicted, actual, change, step).
//   explainRows(events, model, team, key, n = 10) -> rows (oldest first)
//     events: [event files], key: vector key ("epa", "auto_epa", ...)
//   explainPanel({team, label, rows, model, modelLabel, you}) -> SafeHTML
import { html } from '../core/html.js';
import { tbl, derivePre } from '../core/data.js';
import { rating, delta, matchLabel } from '../core/fmt.js';
import { dataTable } from './table.js';

export function explainRows(events, model, team, key, n = 10) {
  const all = [];
  for (const ev of events) {
    const tm = ev.team_matches;
    const post = tm.models[model];
    const attr = post[`attr_${key}`];
    if (!post[key]) continue;
    const pre = derivePre(ev, model);
    const mt = tbl(ev.matches);
    for (let r = 0; r < tm.n; r++) {
      if (tm.cols.team[r] !== team) continue;
      const after = post[key][r];
      if (after === null || after === undefined) continue;
      const before = pre[r] ? pre[r][key] : null;
      const actual = attr ? attr[r] : null;
      const mi = tm.cols.m[r];
      const gap = typeof actual === 'number' && typeof before === 'number' ? actual - before : null;
      const change = typeof before === 'number' ? after - before : null;
      all.push({
        seq: mt.get(mi, 'seq'),
        match: matchLabel(mt.get(mi, 'comp_level'), mt.get(mi, 'set_number'), mt.get(mi, 'match_number')),
        event: ev.meta.key,
        before, actual, after, change,
        step: gap !== null && Math.abs(gap) > 1e-9 && change !== null ? change / gap : null,
      });
    }
  }
  all.sort((a, b) => a.seq - b.seq);
  return all.slice(-n);
}

const pctStep = (s) => (typeof s === 'number' ? `${Math.round(s * 100)}%` : '–');

export function explainPanel(o) {
  const cols = [
    { id: 'match', label: 'Match', td: 'team', render: (r) => r.match },
    { id: 'before', label: 'Predicted', n: true, render: (r) => rating(r.before) },
    { id: 'actual', label: 'Actual', n: true, render: (r) => rating(r.actual) },
    { id: 'change', label: 'Change', n: true, render: (r) => html`<span class="pw-delta ${r.change > 0 ? 'pw-delta--up' : r.change < 0 ? 'pw-delta--down' : ''}">${delta(r.change)}</span>` },
    { id: 'after', label: 'New rating', n: true, render: (r) => rating(r.after) },
    { id: 'step', label: 'Step', n: true, render: (r) => pctStep(r.step) },
  ];
  const first = o.rows[0];
  const last = o.rows[o.rows.length - 1];
  return html`<section class="pw-explain" id="pw-explain" role="region" aria-labelledby="pw-explain-t" tabindex="-1">
<div class="pw-explain__head">
<div>
<h2 class="pw-figure__title" id="pw-explain-t">How ${o.team}'s ${o.label} was last updated</h2>
<p class="pw-figure__sub">${o.rows.length ? html`Last ${o.rows.length} updates, ${first.match} to ${last.match}, ${o.modelLabel}. Points per match.` : 'No updates yet this season.'}</p>
</div>
<button type="button" class="pw-btn" data-act="explain-close">Close</button>
</div>
${o.rows.length ? dataTable({ columns: cols, rows: o.rows, label: `Last updates to ${o.team} ${o.label}` }) : ''}
<p class="pw-caption"><b>Predicted:</b> the rating before the match. <b>Actual:</b> what the model credited the team with in that match, after splitting the alliance score among its three robots. <b>Change:</b> new rating minus predicted. <b>Step:</b> the share of the predicted-to-actual gap the rating moved. The model is ${o.modelLabel}; these are its own estimates, not match results.</p>
</section>`;
}
