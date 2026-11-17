import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutGraph} from '../../web/graph-layout.js';

const edge=(id,unit='one')=>({id,unit,file:'source.md',subject_label:'Board',subject_kind:'component',object_label:'DDR4',object_kind:'memory',predicate:'supports',review:'accepted',verification:'supported'});
test('the graph only presents source-backed accepted relationships',()=>{
  const rows=[edge('ok'),{...edge('pending'),verification:'pending'},{...edge('rejected'),review:'rejected'}];
  assert.deepEqual(layoutGraph(rows).edges.map(row=>row.id),['ok']);
});
test('display grouping never joins homonyms from distinct evidence units',()=>{
  const result=layoutGraph([edge('first','unit-a'),edge('second','unit-b')]);
  assert.equal(result.nodes.length,4);assert.equal(result.groups.length,2);
  assert.notEqual(result.edges[0].source,result.edges[1].source);
});
test('searching and layout preserve real source references',()=>{
  const result=layoutGraph([edge('first'),{...edge('other'),subject_label:'Contract',object_label:'Audit'}],'DDR4');
  assert.equal(result.edges.length,1);assert.equal(result.edges[0].row.id,'first');
  assert.ok(result.nodes.every(row=>Number.isFinite(row.x)&&Number.isFinite(row.y)));
});
