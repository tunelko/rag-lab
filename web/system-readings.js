// Pure helpers of the system status panel: which illustration stands for the graphics card, the points of its small chart,
// the rolling log of readings and the order of the process table. No DOM here, so they are unit-tested.

// Which illustration stands for a device, from the name the system reports. The exact model is shown as text;
// the picture only tells the family apart: vendor, tier and kind of cooling.
export function gpuFamily(name = '') {
  const text = String(name).replace(/[™®]|\((tm|r)\)/gi, '').toLowerCase();
  const nvidia = /nvidia|geforce|quadro|tesla|\brtx\b|\bgtx\b|titan/.test(text), amd = /\bamd\b|radeon/.test(text), intel = /intel|\barc\b|iris|\buhd\b/.test(text);
  if (/\buhd\b|iris|\bhd graphics|integrated/.test(text) || (amd && /graphics/.test(text) && !/\brx\b/.test(text))) return 'integrated';
  if (nvidia) {
    if (/tesla|\b(a100|a10g?|a16|a2|a30|a40|h100|h200|l4|l40s?|t4|v100|p100|p40|p4|k80)\b/.test(text)) return 'nvidia-datacenter';
    if (/quadro|\brtx\s*a\d{4}|\bada\b/.test(text)) return 'nvidia-pro';
    if (/titan/.test(text)) return 'nvidia-high';
    const rtx = text.match(/\brtx\s*(\d{4})/);
    if (rtx) return /[89]0$/.test(rtx[1]) ? 'nvidia-high' : 'nvidia-mid';
    return /\bgtx?\b/.test(text) ? 'nvidia-gtx' : 'nvidia-mid';
  }
  if (amd) { const rx = text.match(/\brx\s*(\d{3,4})/); return rx && rx[1].length === 4 && /^\d[89]/.test(rx[1]) ? 'amd-high' : 'amd-mid'; }
  if (intel && /\barc\b/.test(text)) return 'intel-arc';
  return 'generic';
}

// The model as it is written under the picture: the name without the vendor words ("RTX 3080").
export function gpuShort(name = '') {
  const words = String(name).replace(/[™®]|\((tm|r)\)/gi, '').split(/\s+/).filter(word => word && !/^(nvidia|amd|intel|geforce|radeon|corporation|graphics|series|generation)$/i.test(word));
  return words.join(' ') || String(name).trim() || 'GPU';
}

// Whether the device counts as working. Two thresholds, so the picture does not flicker around one value.
export function gpuBusy(previous, use) {
  if (use == null) return false;
  return previous ? use > 10 : use >= 25;
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
