import test from 'node:test';
import assert from 'node:assert/strict';
import {documentTitle, groupSources, fragmentPosition, relevance} from '../../web/chat-sources.js';

const source = (citation, file, distance, extra = {}) => ({citation, file, cosine_distance: distance, text: 'texto', chunk: 3, ...extra});

test('a readable title comes from the first heading or from the file name', () => {
  assert.equal(documentTitle('receta-facil-con-chocolate_recipe_10c8e7ab.md'), 'Receta facil con chocolate');
  assert.equal(documentTitle('carpeta/manual_de_placa-base.PDF'), 'Manual de placa base');
  assert.equal(documentTitle('informe-2024-3f9a1c2b77.txt'), 'Informe 2024');
  assert.equal(documentTitle('notas.md', [{chunk: 1, text: '# Bizcocho de claras\n\nIngredientes…'}]), 'Bizcocho de claras');
  // A heading inside a later fragment is a section, not the document title.
  assert.equal(documentTitle('notas.md', [{chunk: 4, text: '# Preparación\n\nMezclar…'}]), 'Notas');
  assert.equal(documentTitle('.md'), '.md');
});

test('fragments of one document share a row, ordered by their best relevance', () => {
  const groups = groupSources([source(1, 'a.md', .13), source(2, 'b.md', .14), source(3, 'b.md', .30), source(4, 'c.md', .05)]);
  assert.deepEqual(groups.map(group => [group.file, group.citations]), [['c.md', [4]], ['a.md', [1]], ['b.md', [2, 3]]]);
  assert.equal(groups[2].relevance, .86);
  assert.equal(groups[2].fragments.length, 2);
  assert.deepEqual(groupSources([]), []);
  // Equal relevance keeps the order in which the answer cites the documents.
  assert.deepEqual(groupSources([source(1, 'x.md', .2), source(2, 'y.md', .2)]).map(group => group.file), ['x.md', 'y.md']);
});

test('a citation knows its place among the fragments of its document', () => {
  const sources = [source(1, 'a.md', .1), source(2, 'b.md', .1), source(3, 'b.md', .2)];
  assert.deepEqual([fragmentPosition(sources, 3).index, fragmentPosition(sources, 3).total], [2, 2]);
  assert.deepEqual([fragmentPosition(sources, '1').index, fragmentPosition(sources, '1').total], [1, 1]);
  assert.equal(fragmentPosition(sources, 9), null);
});

test('relevance prefers the server value and stays within 0 and 1', () => {
  assert.equal(relevance({relevance: .42, cosine_distance: .1}), .42);
  assert.equal(relevance({cosine_distance: 1.4}), 0);
  assert.equal(relevance({}), 1);
});
