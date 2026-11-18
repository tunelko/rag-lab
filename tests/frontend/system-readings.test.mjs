import test from 'node:test';
import assert from 'node:assert/strict';
import {gpuFamily, gpuBusy, sparkPoints, pushReading, sortProcesses} from '../../web/system-readings.js';

test('the illustration follows the family of the device named by the system', () => {
  const cases = {
    'NVIDIA GeForce RTX 3080': 'nvidia-high', 'NVIDIA GeForce RTX 4090': 'nvidia-high', 'NVIDIA TITAN RTX': 'nvidia-high',
    'NVIDIA GeForce RTX 4070 Ti SUPER': 'nvidia-mid', 'NVIDIA GeForce RTX 3060': 'nvidia-mid',
    'NVIDIA GeForce GTX 1660 SUPER': 'nvidia-gtx', 'NVIDIA GeForce GT 1030': 'nvidia-gtx',
    'NVIDIA RTX A4000': 'nvidia-pro', 'Quadro P2000': 'nvidia-pro', 'NVIDIA RTX 6000 Ada Generation': 'nvidia-pro',
    'Tesla T4': 'nvidia-datacenter', 'NVIDIA A100-SXM4-40GB': 'nvidia-datacenter', 'NVIDIA L4': 'nvidia-datacenter',
    'AMD Radeon RX 7900 XT': 'amd-high', 'AMD Radeon RX 6800': 'amd-high', 'AMD Radeon RX 6600 XT': 'amd-mid', 'Radeon RX 580 Series': 'amd-mid',
    'Intel(R) Arc(TM) A770 Graphics': 'intel-arc',
    'Intel(R) UHD Graphics 770': 'integrated', 'Intel(R) Iris(R) Xe Graphics': 'integrated', 'AMD Radeon(TM) Graphics': 'integrated',
    'Matrox G200': 'generic', '': 'generic',
  };
  for (const [name, family] of Object.entries(cases)) assert.equal(gpuFamily(name), family, name);
});

test('working starts at a quarter of use and ends near idle, so the picture does not flicker', () => {
  assert.equal(gpuBusy(false, 24), false);
  assert.equal(gpuBusy(false, 25), true);
  assert.equal(gpuBusy(true, 12), true);
  assert.equal(gpuBusy(true, 10), false);
  assert.equal(gpuBusy(true, null), false);
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
