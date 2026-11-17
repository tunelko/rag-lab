import test from 'node:test';
import assert from 'node:assert/strict';
import {placeIslands, kindTones, rowMatches} from '../../web/graph-layout.js';

const group = count => ({nodes: Array.from({length: count}, () => ({}))});

test('every entity gets its own floor cell, placed on the isometric axes (two across, one down)', () => {
  const groups = [group(8), group(2), group(3)];
  const size = placeIslands(groups, {cell: 66, width: 960});
  for (const island of groups) {
    const seen = new Set();
    for (const item of island.nodes) {
      // Back from screen to floor cells: both coordinates land on the half-cell grid inside the island.
      const dx = (item.x - island.x) / 66, dy = (item.y - island.y) / 33, u = (dy + dx) / 2, v = (dy - dx) / 2;
      assert.ok(Math.abs(u % 1 - .5) < 1e-9 && Math.abs(v % 1 - .5) < 1e-9);
      assert.ok(u > 0 && u < island.side && v > 0 && v < island.side);
      seen.add(u + ':' + v);
    }
    assert.equal(seen.size, island.nodes.length);
  }
  assert.equal(groups[0].side, 3);
  assert.ok(size.width > 0 && size.height > 0);
});

test('islands tile like bricks: rows shift half a step and never grow wider than the canvas allows', () => {
  const groups = Array.from({length: 7}, () => group(4));
  const size = placeIslands(groups, {cell: 66, width: 960});
  const rows = [...new Set(groups.map(island => island.y))];
  assert.ok(rows.length > 1);
  const first = groups.filter(island => island.y === rows[0]), second = groups.filter(island => island.y === rows[1]);
  assert.ok(first.length > 1);
  assert.equal(second[0].x - first[0].x, (first[1].x - first[0].x) / 2);
  assert.ok(size.width <= 960 + (first[1].x - first[0].x));
});

test('the commonest entity types get the colours, at most five', () => {
  const tones = kindTones([{kind: 'Tool'}, {kind: 'tool'}, {kind: 'Group'}, {kind: ''}, {kind: 'a'}, {kind: 'b'}, {kind: 'c'}, {kind: 'd'}]);
  assert.equal(tones.get('tool'), 0);
  assert.equal(tones.size, 5);
  assert.equal(tones.has('group'), false);
});

test('search and source decide what stays bright, without touching the data', () => {
  const row = {subject_label: 'APT1', object_label: 'Mimikatz', predicate: 'usa', file: 'cti/a.md'};
  assert.equal(rowMatches(row, 'mimi', ''), true);
  assert.equal(rowMatches(row, 'zzz', ''), false);
  assert.equal(rowMatches(row, '', 'cti/b.md'), false);
  assert.equal(rowMatches(row, ' USA ', 'cti/a.md'), true);
});
