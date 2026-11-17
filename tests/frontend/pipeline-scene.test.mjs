import test from 'node:test';
import assert from 'node:assert/strict';
import {sceneState} from '../../web/pipeline-scene.js';

test('the scene lights the stages a view acts on and the finished ones', () => {
  assert.deepEqual(sceneState('rel'), {on: true, run: false, error: false});
  assert.deepEqual(sceneState('done'), {on: true, run: false, error: false});
});

test('only the working stage beats, and it is lit too', () => {
  assert.deepEqual(sceneState('run'), {on: true, run: true, error: false});
});

test('idle and unknown stages stay off; a failed stage is marked, not lit', () => {
  assert.deepEqual(sceneState('idle'), {on: false, run: false, error: false});
  assert.deepEqual(sceneState(undefined), {on: false, run: false, error: false});
  assert.deepEqual(sceneState('error'), {on: false, run: false, error: true});
});

test('the explained scene stands along one isometric axis: two across, one down', async () => {
  const {diagonalCentre} = await import('../../web/pipeline-scene.js');
  const first = diagonalCentre(0), last = diagonalCentre(5);
  assert.equal((last.x - first.x) / (last.y - first.y), 2);
  assert.ok(diagonalCentre(1).x > first.x && diagonalCentre(1).y > first.y);
});

test('arrow keys walk the stages and wrap; Home and End jump', async () => {
  const {nextStage} = await import('../../web/pipeline-scene.js');
  assert.equal(nextStage(0, 'ArrowRight'), 1);
  assert.equal(nextStage(5, 'ArrowRight'), 0);
  assert.equal(nextStage(0, 'ArrowLeft'), 5);
  assert.equal(nextStage(3, 'Home'), 0);
  assert.equal(nextStage(3, 'End'), 5);
  assert.equal(nextStage(3, 'Enter'), 3);
});
