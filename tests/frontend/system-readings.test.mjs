import test from 'node:test';
import assert from 'node:assert/strict';
import {gpuLabel, sparkPoints, pushReading, sortProcesses} from '../../web/system-readings.js';

test('the card carries the series and model of the device, without the vendor words', () => {
  assert.deepEqual(gpuLabel('NVIDIA GeForce RTX 3080'), {series: 'RTX', model: '3080'});
  assert.deepEqual(gpuLabel('NVIDIA GeForce RTX 4070 Ti SUPER'), {series: 'RTX', model: '4070 Ti'});
  assert.deepEqual(gpuLabel('AMD Radeon RX 7900 XT'), {series: 'RX', model: '7900 XT'});
  assert.deepEqual(gpuLabel('Intel(R) Arc(TM) A770 Graphics'), {series: 'ARC', model: 'A770'});
  assert.deepEqual(gpuLabel(''), {series: 'GPU', model: ''});
});

test('the chart spans the box, clamps to its range and skips missing readings', () => {
  assert.equal(sparkPoints([0, 50, 100], 100, 300, 56), '0,56 150,28 300,0');
  assert.equal(sparkPoints([0, null, 250], 100, 300, 56), '0,56 300,0');
  assert.equal(sparkPoints([40], 100), '');
  assert.equal(sparkPoints([1, 2], 0), '');
});

test('the log keeps the newest readings first and ignores a repeated sample', () => {
  const device = {utilization_percent: 23, memory_used_bytes: 10, temperature_c: 41.4, power_w: 30};
  let log = pushReading([], 't1', device);
  log = pushReading(log, 't1', device);
  assert.equal(log.length, 1);
  for (const time of ['t2', 't3', 't4', 't5', 't6']) log = pushReading(log, time, device);
  assert.deepEqual(log.map(row => row.time), ['t6', 't5', 't4', 't3', 't2']);
  assert.equal(pushReading(log, 't7', null), log);
  assert.equal(pushReading([], 't1', {})[0].use, null);
});

test('the process table keeps the server order until a column is chosen', () => {
  const rows = [{pid: 3, name: 'python.exe', cpu_percent: 1.5, ram_bytes: 30}, {pid: 1, name: 'ollama.exe', cpu_percent: 9, ram_bytes: 10}, {pid: 2, name: 'llama-server.exe', cpu_percent: null, ram_bytes: 20}];
  assert.deepEqual(sortProcesses(rows).map(row => row.pid), [3, 1, 2]);
  assert.deepEqual(sortProcesses(rows, 'name').map(row => row.pid), [2, 1, 3]);
  assert.deepEqual(sortProcesses(rows, 'cpu_percent', true).map(row => row.pid), [1, 3, 2]);
  assert.deepEqual(sortProcesses(rows, 'ram_bytes').map(row => row.pid), [1, 2, 3]);
  assert.equal(rows[0].pid, 3);
});
