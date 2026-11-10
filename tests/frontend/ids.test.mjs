import test from 'node:test';
import assert from 'node:assert/strict';
import {randomId} from '../../dist/ids.js';

test('private HTTP fallback generates UUID v4 when randomUUID is unavailable',()=>{
  const provider={getRandomValues(array){array.fill(255);return array;}};
  assert.equal(randomId(provider),'ffffffff-ffff-4fff-bfff-ffffffffffff');
});

test('fallback IDs use fresh random bytes and preserve the UUID version/variant',()=>{
  const provider={getRandomValues:array=>globalThis.crypto.getRandomValues(array)};
  const values=Array.from({length:100},()=>randomId(provider));
  assert.equal(new Set(values).size,100);
  assert.ok(values.every(value=>/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value)));
});
