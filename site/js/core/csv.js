// CSV export (WP5).
//
//   toCSV(columns, rows) -> string    RFC 4180 fields, "\n" line ends, header row first.
//       columns: [{key, label, value?}]; the cell is row[key], or value(row) when given.
//       null/undefined/NaN -> empty cell. Numbers are written as plain numbers (full precision as
//       given, so round before passing if the table shows rounded values).
//       Fields with a comma, quote, CR or LF are double-quoted with inner quotes doubled.
//       Text that a spreadsheet would run as a formula (starts with = + @ tab CR, or - followed
//       by a non-digit) gets a leading apostrophe.
//   csvFilename({view, season, region, asof, ext}) -> "pitwall-{view}-{season}[-{region}][-{asof}].csv"
//   download(filename, text)          saves a UTF-8 file (with BOM so Excel reads it right).

function cell(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  let s = String(v);
  if (/^[=+@\t\r]/.test(s) || /^-(?!\d)/.test(s)) s = `'${s}`;
  if (/[",\r\n]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
  return s;
}

function headerCell(v) {
  // Headers are our own labels; quote per RFC 4180 but do not prefix an apostrophe.
  const s = String(v ?? '');
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCSV(columns, rows) {
  const lines = [columns.map((c) => headerCell(c.label ?? c.key)).join(',')];
  for (const row of rows) {
    lines.push(columns.map((c) => cell(typeof c.value === 'function' ? c.value(row) : row[c.key])).join(','));
  }
  return lines.join('\n') + '\n';
}

function slug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function csvFilename({ view, season, region, asof, ext = 'csv' } = {}) {
  const parts = ['pitwall', view, season, region, asof]
    .filter((p) => p !== undefined && p !== null && p !== '' && p !== 'all')
    .map(slug)
    .filter(Boolean);
  return `${parts.join('-')}.${ext}`;
}

export function download(filename, text) {
  const blob = new Blob(['﻿', text], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
