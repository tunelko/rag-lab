// Pure helpers of the system status panel: the label printed on the drawn graphics card, the points of its small chart,
// the rolling log of readings and the order of the process table. No DOM here, so they are unit-tested.

const VENDOR = /^(nvidia|amd|intel|intel\(r\)|geforce|radeon|\(tm\)|\(r\)|graphics|corporation)$/i;
const SERIES = /^(rtx|gtx|gt|rx|arc|quadro|tesla|titan|iris|uhd)$/i;

// "NVIDIA GeForce RTX 3080" -> {series: "RTX", model: "3080"}: the two lines printed on the card.
export function gpuLabel(name = '') {
  const words = String(name).replace(/[™®]|\((tm|r)\)/gi, '').split(/\s+/).filter(word => word && !VENDOR.test(word));
  if (!words.length) return {series: 'GPU', model: ''};
  const at = words.findIndex(word => SERIES.test(word));
  if (at < 0) return {series: 'GPU', model: words.slice(0, 2).join(' ').slice(0, 12)};
  return {series: words[at].toUpperCase(), model: words.slice(at + 1, at + 3).join(' ').slice(0, 12)};
}

// The points of a polyline for `values` (null breaks nothing: it is skipped) on a width x height box, 0 at the bottom.
export function sparkPoints(values, max = 100, width = 300, height = 56) {
  const known = values.map((value, index) => [index, value]).filter(([, value]) => value != null && Number.isFinite(value));
  if (known.length < 2 || !(max > 0)) return '';
  const last = values.length - 1, round = value => Math.round(value * 10) / 10;
  return known.map(([index, value]) => `${round(index / last * width)},${round(height - Math.max(0, Math.min(1, value / max)) * height)}`).join(' ');
}

// One reading of the device, as the log keeps it. Returns the log with the reading in front, unless it is the same sample.
export function pushReading(log, time, device, limit = 5) {
  if (!device || !time || log[0]?.time === time) return log;
  return [{time, use: device.utilization_percent ?? null, vram: device.memory_used_bytes ?? null, heat: device.temperature_c ?? null, power: device.power_w ?? null}, ...log].slice(0, limit);
}

// The rows of the process table in the chosen order; without a key they stay as the server sent them.
export function sortProcesses(rows, key = '', descending = false) {
  if (!key) return [...rows];
  const value = row => key === 'name' ? String(row.name || '').toLocaleLowerCase() : Number(row[key] ?? -1);
  const sorted = [...rows].sort((a, b) => { const x = value(a), y = value(b); return x < y ? -1 : x > y ? 1 : a.pid - b.pid; });
  return descending ? sorted.reverse() : sorted;
}
