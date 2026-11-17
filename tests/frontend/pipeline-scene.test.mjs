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
