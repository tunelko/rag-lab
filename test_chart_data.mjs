import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeHistory,metricValue,chartSeries} from './dist/chart-data.js';

const time='2026-11-10T10:00:00Z';
const epoch=Date.parse(time);
const data={time,cpu_percent:12.5,ram_used_bytes:8*1024**3,ram_total_bytes:32*1024**3,gpu:{devices:[{utilization_percent:85,memory_used_bytes:8*1024**3,memory_total_bytes:10*1024**3}]}};

test('repeated cached samples do not invent history',()=>{
  const first=mergeHistory([],data);const repeated=mergeHistory(first,data);
  assert.equal(first.length,1);assert.equal(repeated.length,1);assert.equal(repeated[0].gpu_percent,85);
});
test('history is time ordered and bounded to fifteen minutes',()=>{
  const history=mergeHistory([{time:'2026-11-10T09:00:00Z',cpu_percent:90}],{...data,history:[{time:'2026-11-10T09:59:58Z',cpu_percent:10}]});
  assert.equal(history.length,2);assert.ok(history[0].epoch<history[1].epoch);
});
test('missing readings and long gaps break the line',()=>{
  const point=(seconds,value)=>({time:new Date(epoch+seconds*1000).toISOString(),epoch:epoch+seconds*1000,cpu_percent:value});
  const series=chartSeries([point(0,10),point(2,null),point(4,20),point(25,30)],'cpu',epoch,epoch+60000,100);
  assert.deepEqual(series.segments.map(segment=>segment.length),[1,1,1]);assert.equal(series.mean,20);
});
test('memory graphs use GiB and percentage graphs keep a 0–100 scale',()=>{
  const points=mergeHistory([],data);assert.equal(metricValue(points[0],'ram'),8);
  const series=chartSeries(points,'cpu',epoch-60000,epoch,100);
  assert.equal(series.values[0].x,596);assert.equal(series.values[0].y,168-12.5/100*158);
});
test('an unavailable sensor is a gap, not zero load',()=>{
  const points=mergeHistory([],{...data,gpu:{devices:[]}});const series=chartSeries(points,'gpu',epoch-60000,epoch,100);
  assert.equal(series.values.length,0);assert.equal(series.mean,null);
});
