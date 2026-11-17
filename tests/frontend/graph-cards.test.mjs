import test from 'node:test';
import assert from 'node:assert/strict';
import {placeStars, kindTones, rowMatches} from '../../web/graph-layout.js';

const passage = (prefix, count) => ({nodes: Array.from({length: count}, (_, index) => ({id: prefix + index}))});

test('a passage is a star: its most connected entity in the middle, the others around with room for their cards', () => {
  const group = passage('a', 6), edges = [1, 2, 3, 4, 5].map(index => ({source: 'a2', target: 'a' + index}));
  const size = placeStars([group], edges);
  const hub = group.nodes.find(item => item.id === 'a2');
  assert.deepEqual([hub.x, hub.y], [group.x, group.y]);
  for (const a of group.nodes) for (const b of group.nodes) {
    if (a === b) continue;
    // Cards are up to about 200 px wide and 44 px tall: no two may share that box.
    assert.ok(Math.abs(a.x - b.x) >= 200 || Math.abs(a.y - b.y) >= 50, `${a.id} and ${b.id} would overlap`);
  }
  assert.ok(size.width >= 2 * group.rx && size.height >= 2 * group.ry);
});

test('nothing sits right above the middle card, where the name of the passage goes', () => {
  for (const count of [2, 3, 4, 5, 9]) {
    const group = passage('n', count);
    placeStars([group]);
    for (const item of group.nodes.slice(1)) assert.ok(item.y >= group.y || Math.abs(item.x - group.x) > 60, `count ${count}`);
  }
});

test('passages sit side by side and wrap into rows', () => {
  const groups = Array.from({length: 5}, (_, index) => passage('g' + index + '-', 4));
  const size = placeStars(groups, [], {rowWidth: 1600});
  assert.ok(groups[1].x - groups[0].x >= 2 * groups[0].rx + 180);
  assert.ok(new Set(groups.map(group => group.y)).size > 1);
  assert.ok(size.width <= 1600);
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
