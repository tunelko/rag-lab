import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceScope} from '../../web/source-scope.js';

const status={library:{topics:[{id:'recipes'},{id:'cakes',parent_id:'recipes'},{id:'empty'}]},documents:[
  {name:'recipes.pdf',topic_id:'recipes',indexed:true},
  {name:'cakes.pdf',topic_id:'cakes',indexed:true},
  {name:'waiting.pdf',topic_id:'cakes',indexed:false},
]};

test('an empty selected source stays empty while the whole library and child categories have content',()=>{
  assert.deepEqual(sourceScope(status,'unassigned'),{exists:true,documents:0,indexed:0,elsewhere:true});
  assert.deepEqual(sourceScope(status,'recipes'),{exists:true,documents:3,indexed:2,elsewhere:false});
  assert.deepEqual(sourceScope(status,'cakes'),{exists:true,documents:2,indexed:1,elsewhere:true});
  assert.deepEqual(sourceScope(status,''),{exists:true,documents:3,indexed:2,elsewhere:false});
});

test('a deleted source never inherits the whole library and an unindexed upload is not searchable',()=>{
  assert.deepEqual(sourceScope(status,'deleted'),{exists:false,documents:0,indexed:0,elsewhere:true});
  assert.deepEqual(sourceScope({documents:[{indexed:false}]},'unassigned'),{exists:true,documents:1,indexed:0,elsewhere:false});
});
