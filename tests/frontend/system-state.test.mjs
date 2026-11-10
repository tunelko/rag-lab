import test from 'node:test';
import assert from 'node:assert/strict';
import {vramLevel} from '../../dist/system-state.js';
test('VRAM thresholds follow measured capacity and concurrent models',()=>{
  assert.equal(vramLevel(90,100),'info');
  assert.equal(vramLevel(95,100),'warn');
  assert.equal(vramLevel(97,100),'warn');
  assert.equal(vramLevel(98,100),'crit');
  assert.equal(vramLevel(10,100,2),'crit');
  assert.equal(vramLevel(null,null),'info');
  assert.equal(vramLevel(0,0),'info');
});
