// Theme tokens (SPEC 5.7): every colour token in tokens.json appears in the light block, the
// [data-theme="dark"] block and the prefers-color-scheme dark block with the right value; no
// stylesheet carries a literal colour; tokens.css is what build_tokens.py would write.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';

const SITE = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = join(SITE, '..');
const tokens = JSON.parse(readFileSync(join(ROOT, 'docs/design/tokens.json'), 'utf8'));
const css = readFileSync(join(SITE, 'css/tokens.css'), 'utf8');

function block(re) {
  const m = re.exec(css);
  assert.ok(m, `block ${re} present`);
  let depth = 0; let i = m.index + m[0].length - 1; const start = i + 1;
  for (; i < css.length; i++) { if (css[i] === '{') depth++; else if (css[i] === '}') { depth--; if (depth === 0) break; } }
  return css.slice(start, i);
}

test('every colour token is defined in both themes with the tokens.json value', () => {
  const light = block(/:root\s*\{/);
  const dark = block(/:root\[data-theme="dark"\]\s*\{/);
  const media = block(/@media \(prefers-color-scheme: dark\)\s*\{/);
  for (const [name, v] of Object.entries(tokens.color)) {
    const want = (b, val) => assert.match(b, new RegExp(`--${name}:\\s*${val.replace(/[#()]/g, '\\$&')};`, 'i'), `--${name} = ${val}`);
    want(light, v.light);
    want(dark, v.dark);
    want(media, v.dark);
  }
  assert.match(light, /color-scheme:\s*light/);
  assert.match(dark, /color-scheme:\s*dark/);
});

test('stylesheets use token variables only (no literal colours)', () => {
  const files = ['css/pitwall.css', ...readdirSync(join(SITE, 'css/views')).map((f) => `css/views/${f}`)];
  for (const f of files) {
    const text = readFileSync(join(SITE, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    assert.doesNotMatch(text, /#[0-9a-f]{3,8}\b/i, `${f} has a hex colour`);
    assert.doesNotMatch(text, /rgba?\(/i, `${f} has an rgb() colour`);
  }
});

test('tokens.css matches build_tokens.py output', () => {
  const dst = join(mkdtempSync(join(tmpdir(), 'pw-tok-')), 'tokens.css');
  try {
    execFileSync('python3', ['-I', join(SITE, 'tools/build_tokens.py'), join(ROOT, 'docs/design/tokens.json'), dst], { stdio: 'ignore' });
  } catch (e) { if (e.code === 'ENOENT') return; throw e; } // no python: the other tests still guard the values
  assert.equal(readFileSync(dst, 'utf8'), css);
});
