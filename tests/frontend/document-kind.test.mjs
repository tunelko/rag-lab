import test from 'node:test';
import assert from 'node:assert/strict';
import {documentKind, splitFrontMatter, KIND_LABEL} from '../../web/document-kind.js';

test('the viewer reads a text preview with a Markdown name as Markdown, and keeps PDFs and images as they are', () => {
  assert.equal(documentKind('guia.md', 'text'), 'markdown');
  assert.equal(documentKind('cti/groups/APT1.MARKDOWN', 'text'), 'markdown');
  assert.equal(documentKind('notas.txt', 'text'), 'text');
  assert.equal(documentKind('manual.pdf', 'pdf'), 'pdf');
  assert.equal(documentKind('foto.md', 'image'), 'image');
  assert.equal(documentKind('', 'text'), 'text');
  assert.deepEqual(Object.keys(KIND_LABEL).sort(), ['image', 'markdown', 'pdf', 'text']);
});

test('front matter is split from the body: keys, quoted values and lists', () => {
  const {meta, body} = splitFrontMatter('---\ntitle: "Night Dragon"\ntags:\n  - campaign\n  - apt\nsource: mitre\n---\n# Night Dragon\n\nText.');
  assert.deepEqual(meta, [['title', 'Night Dragon'], ['tags', 'campaign, apt'], ['source', 'mitre']]);
  assert.equal(body, '# Night Dragon\n\nText.');
});

test('text that only looks like front matter stays in the body', () => {
  const rule = '---\nThis is a paragraph between two rules.\n---\nMore.';
  assert.deepEqual(splitFrontMatter(rule), {meta: [], body: rule});
  assert.deepEqual(splitFrontMatter('# Title\n\n---\nkey: value\n---\n'), {meta: [], body: '# Title\n\n---\nkey: value\n---\n'});
  assert.deepEqual(splitFrontMatter(''), {meta: [], body: ''});
  assert.deepEqual(splitFrontMatter('---\n---\nBody'), {meta: [], body: '---\n---\nBody'});
});

test('Windows line ends and a leading byte order mark do not break the split', () => {
  const {meta, body} = splitFrontMatter('﻿---\r\nid: T1486\r\n---\r\nBody');
  assert.deepEqual(meta, [['id', 'T1486']]);
  assert.equal(body, 'Body');
});

test('a block written as one JSON object is read as front matter too', () => {
  const {meta, body} = splitFrontMatter('---\n{"id": "campaign--1", "revoked": false, "aliases": ["A", "B"], "source": {"name": "mitre"}, "note": null}\n---\n\n# Title');
  assert.deepEqual(meta, [['id', 'campaign--1'], ['revoked', 'false'], ['aliases', 'A, B'], ['source', '{"name":"mitre"}'], ['note', '']]);
  assert.equal(body, '\n# Title');
  assert.deepEqual(splitFrontMatter('---\n{broken json\n---\nBody').meta, []);
});
