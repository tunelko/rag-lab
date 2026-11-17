import test from 'node:test';
import assert from 'node:assert/strict';
import {citedCount, journey, scopeChunks} from '../../web/chat-journey.js';

const sources = [
  {citation: 1, file: 'a.md', cosine_distance: 0.13},
  {citation: 2, file: 'a.md', cosine_distance: 0.2},
  {citation: 3, file: 'b.md', cosine_distance: 0.4},
];

test('an empty chat shows the two stages a question will use', () => {
  const view = journey(null);
  assert.equal(view.phase, 'empty');
  assert.deepEqual(view.scene, ['idle', 'idle', 'idle', 'idle', 'rel', 'rel']);
  assert.ok(Object.values(view.checks).every(state => state === 'todo'));
});

test('the list is ticked as the job advances', () => {
  const retrieving = journey({status: 'running', sources: [], metrics: {}});
  assert.equal(retrieving.phase, 'retrieving');
  assert.equal(retrieving.checks.vector, 'run');
  assert.deepEqual(retrieving.scene.slice(4), ['run', 'idle']);
  assert.equal(retrieving.step, 5);

  const generating = journey({status: 'running', sources, answer: 'Una [1]', model: 'gemma',
    metrics: {vector_search_seconds: 0.1, routing_mode: 'automatic'}});
  assert.equal(generating.phase, 'generating');
  assert.deepEqual(generating.checks, {vector: 'done', fragments: 'done', route: 'done', answer: 'run', cited: 'todo'});
  assert.deepEqual(generating.scene.slice(4), ['done', 'run']);
  assert.equal(generating.step, 6);

  const done = journey({status: 'done', sources, answer: 'Una [1] y otra [3]. Inventada [9].', model: 'gemma',
    metrics: {vector_search_seconds: 0.1, routing_mode: 'automatic', output_tokens: 20, generation_tok_s: 5}});
  assert.equal(done.phase, 'done');
  assert.ok(Object.values(done.checks).every(state => state === 'done'));
  assert.deepEqual(done.scene.slice(4), ['done', 'done']);
  assert.equal(done.facts.cited, 2);
  assert.equal(done.facts.documents, 2);
  assert.equal(done.facts.fragments, 3);
  assert.equal(Number(done.facts.best.toFixed(2)), 0.87);
});

test('search only stops at retrieval and never claims an answer', () => {
  const view = journey({status: 'done', sources, answer: '', metrics: {vector_search_seconds: 0.1, routing_mode: 'search_only'}});
  assert.equal(view.phase, 'retrieved');
  assert.equal(view.checks.answer, 'todo');
  assert.deepEqual(view.scene.slice(4), ['done', 'idle']);
  assert.equal(view.step, 5);
});

test('an inventory answer comes from the register, not from vectors or the model', () => {
  const view = journey({status: 'done', sources: [], answer: 'Hay 3 documentos.', metrics: {routing_mode: 'catalog'}});
  assert.equal(view.phase, 'catalog');
  assert.equal(view.checks.vector, 'todo');
  assert.equal(view.scene[0], 'done');
  assert.deepEqual(view.scene.slice(4), ['idle', 'idle']);
});

test('a failure marks the stage that was working', () => {
  assert.deepEqual(journey({status: 'error', sources: [], metrics: {}}).scene.slice(4), ['error', 'idle']);
  assert.deepEqual(journey({status: 'error', sources, metrics: {vector_search_seconds: 1}}).scene.slice(4), ['done', 'error']);
  assert.equal(journey({status: 'cancelled', sources, answer: 'A medias [1]', metrics: {}}).phase, 'stopped');
});

test('only citations of retrieved fragments count, once each', () => {
  assert.equal(citedCount('[1] [1] [2] [7]', sources), 2);
  assert.equal(citedCount('', sources), 0);
});

test('fragments of the selected source include its subcategories', () => {
  const status = {library: {topics: [{id: 'recipes'}, {id: 'cakes', parent_id: 'recipes'}]},
    documents: [{topic_id: 'recipes', chunks: 4}, {topic_id: 'cakes', chunks: 6}, {chunks: 10}]};
  assert.equal(scopeChunks(status, ''), 20);
  assert.equal(scopeChunks(status, 'recipes'), 10);
  assert.equal(scopeChunks(status, 'cakes'), 6);
  assert.equal(scopeChunks(status, 'unassigned'), 10);
});
