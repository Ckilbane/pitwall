// Region syntax: a district abbreviation ("fma"), "state:PA", "country:USA", or "all".

export const REGIONS = {
  fma: 'FIRST Mid-Atlantic', fim: 'FIRST in Michigan', fit: 'FIRST in Texas', fin: 'FIRST Indiana',
  fnc: 'FIRST North Carolina', fch: 'FIRST Chesapeake', fsc: 'FIRST South Carolina',
  ne: 'New England', pnw: 'Pacific Northwest', ont: 'Ontario', pch: 'Peachtree', isr: 'Israel',
  chs: 'FIRST Chesapeake', mar: 'FIRST Mid-Atlantic', in: 'FIRST Indiana', nc: 'FIRST North Carolina', tx: 'FIRST in Texas',
};

export const STATES = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California', CO: 'Colorado', CT: 'Connecticut',
  DE: 'Delaware', DC: 'District of Columbia', FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois',
  IN: 'Indiana', IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana', ME: 'Maine', MD: 'Maryland',
  MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota', MS: 'Mississippi', MO: 'Missouri', MT: 'Montana',
  NE: 'Nebraska', NV: 'Nevada', NH: 'New Hampshire', NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York',
  NC: 'North Carolina', ND: 'North Dakota', OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon', PA: 'Pennsylvania',
  RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota', TN: 'Tennessee', TX: 'Texas', UT: 'Utah',
  VT: 'Vermont', VA: 'Virginia', WA: 'Washington', WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
  ON: 'Ontario', QC: 'Quebec', BC: 'British Columbia', AB: 'Alberta', MB: 'Manitoba', NS: 'Nova Scotia',
};

export function parseRegion(region) {
  const r = String(region || 'all');
  if (r === 'all') return { kind: 'all' };
  const m = /^(state|country):(.+)$/.exec(r);
  if (m) return { kind: m[1], value: m[2] };
  return { kind: 'district', value: r.toLowerCase() };
}

export function matchRegion(team, region) {
  const p = parseRegion(region);
  switch (p.kind) {
    case 'all': return true;
    case 'district': return (team.district || '').toLowerCase() === p.value;
    case 'state': return team.state === p.value;
    case 'country': return team.country === p.value;
    default: return false;
  }
}

/** Full label: "FIRST Mid-Atlantic", "Pennsylvania", "USA", "All teams". */
export function regionLabel(region) {
  const p = parseRegion(region);
  if (p.kind === 'all') return 'All teams';
  if (p.kind === 'district') return REGIONS[p.value] || p.value.toUpperCase();
  if (p.kind === 'state') return STATES[p.value] || p.value;
  return p.value;
}

/** Short label for sentences: "FMA", "Pennsylvania", "USA", "all teams". */
export function regionShort(region) {
  const p = parseRegion(region);
  if (p.kind === 'all') return 'all teams';
  if (p.kind === 'district') return p.value.toUpperCase();
  return regionLabel(region);
}

/** The region a team is compared in on its own page: its district, else state, else country, else all. */
export function teamRegion(team) {
  if (team.district) return team.district.toLowerCase();
  if (team.state) return `state:${team.state}`;
  if (team.country) return `country:${team.country}`;
  return 'all';
}

/** Options [{id, label, n}] for a season's teams table (cols: district, state, country):
 *  districts present, then All teams, then states and countries with >= 10 teams. */
export function regionOptions(season) {
  const t = season.teams.cols;
  const n = season.teams.n;
  const d = new Map(); const s = new Map(); const c = new Map();
  const bump = (m, k) => { if (k) m.set(k, (m.get(k) || 0) + 1); };
  for (let i = 0; i < n; i++) { bump(d, (t.district[i] || '').toLowerCase()); bump(s, t.state[i]); bump(c, t.country[i]); }
  const out = [];
  [...d.entries()].sort((a, b) => regionLabel(a[0]).localeCompare(regionLabel(b[0])))
    .forEach(([k, v]) => out.push({ id: k, label: regionLabel(k), n: v }));
  out.push({ id: 'all', label: 'All teams', n });
  [...s.entries()].filter(([, v]) => v >= 10).sort((a, b) => regionLabel(`state:${a[0]}`).localeCompare(regionLabel(`state:${b[0]}`)))
    .forEach(([k, v]) => out.push({ id: `state:${k}`, label: regionLabel(`state:${k}`), n: v }));
  [...c.entries()].filter(([, v]) => v >= 10).sort((a, b) => b[1] - a[1])
    .forEach(([k, v]) => out.push({ id: `country:${k}`, label: k, n: v }));
  return out;
}
