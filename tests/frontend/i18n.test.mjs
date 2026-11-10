import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {resolveLanguage, formatMessage} from '../../dist/i18n.js';

const catalogs = Object.fromEntries(['es', 'en'].map(code => [code, JSON.parse(readFileSync(new URL(`../../dist/locales/${code}.json`, import.meta.url), 'utf8'))]));
const slots = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();

test('browser preference order, regional languages and manual selection', () => {
  assert.equal(resolveLanguage('auto', ['fr-FR', 'es-MX', 'en-US']), 'es');
  assert.equal(resolveLanguage('auto', ['en-GB', 'es-ES']), 'en');
  assert.equal(resolveLanguage('auto', ['fr-FR']), 'en');
  assert.equal(resolveLanguage('es', ['en-US']), 'es');
  assert.equal(resolveLanguage('invalid', ['es-ES']), 'es');
});

test('catalogs have identical keys and interpolation placeholders', () => {
  assert.deepEqual(Object.keys(catalogs.es).sort(), Object.keys(catalogs.en).sort());
  for (const key of Object.keys(catalogs.es)) {
    assert.equal(typeof catalogs.en[key], 'string', key);
    assert.ok(catalogs.en[key].trim(), key);
    for (const code of ['es','en']) {
      assert.ok(!catalogs[code][key].includes('\uFFFD'), key);
      assert.ok(!/[\p{L}]\?[\p{L}]/u.test(catalogs[code][key]), key);
    }
    assert.deepEqual(slots(catalogs.es[key]), slots(catalogs.en[key]), key);
  }
});

test('missing translations fall back and interpolation preserves literal user values', () => {
  const fallback = {message: 'Documento {name}: {count}'};
  assert.equal(formatMessage({}, fallback, 'message', {name: '<img onerror=alert(1)>', count: 0}), 'Documento <img onerror=alert(1)>: 0');
  assert.equal(formatMessage({}, {}, 'unknown'), 'unknown');
  assert.equal(formatMessage({}, {}, 'toString'), 'toString');
  assert.equal(formatMessage({message: '{name}'}, {}, 'message', {name: '{count}'}), '{count}');
});

test('all static UI attributes and literal translation calls reference existing keys', () => {
  for (const file of readdirSync(new URL('../../dist/', import.meta.url)).filter(file => file.endsWith('.js') || file === 'index.html')) {
    const source = readFileSync(new URL(`../../dist/${file}`, import.meta.url), 'utf8');
    const keys = [...source.matchAll(/\bt\(['"]([^'"]+)['"]/g), ...source.matchAll(/data-i18n(?:-[a-z-]+)?="([^"]+)"/g)];
    for (const match of keys) assert.ok(Object.hasOwn(catalogs.es, match[1]), `${file}: ${match[1]}`);
  }
});
