// The graphics card and the processes of the system status panel, drawn from the readings the shell already polls:
// a small card with the user's model on it, the last minute as a chart, a rolling log, and a classic task manager.
import {$, node, number} from './ui.js';
import {t} from './i18n.js';
import {gpuFamily, gpuBusy, gpuShort, sparkPoints, pushReading, sortProcesses} from './system-readings.js';

const GIB = 1024 ** 3, clockOf = time => String(time).slice(11, 19);

export class SystemPanel {
  constructor() {
    this.log = []; this.sort = {key: '', descending: false}; this.selected = null; this.data = null; this.busy = false; this.image = '';
    $('taskman-head').addEventListener('click', event => {
      const control = event.target.closest('[data-sort]'); if (!control) return;
      // The same column again turns the order round, as in a task manager.
      this.sort = {key: control.dataset.sort, descending: this.sort.key === control.dataset.sort ? !this.sort.descending : control.dataset.sort !== 'name'};
      this.processes();
    });
    $('hardware-processes').addEventListener('click', event => { const row = event.target.closest('tr'); if (!row) return; this.selected = Number(row.dataset.pid); this.processes(); });
    // Two tabs, as in the classic window: the processes and the performance.
    const tabs = $('taskman-tabs');
    tabs.addEventListener('click', event => { const tab = event.target.closest('[data-tab]'); if (tab) this.show(tab.dataset.tab); });
    tabs.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault(); const other = [...tabs.querySelectorAll('[data-tab]')].find(tab => tab.getAttribute('aria-selected') !== 'true'); this.show(other.dataset.tab); other.focus();
    });
  }

  render(data) {
    this.data = data;
    const device = data?.gpu?.devices?.[0];
    this.log = pushReading(this.log, data?.time, device);
    this.card(device); this.chart(data, device); this.readings(); this.processes(); this.performance();
  }

  show(name) {
    for (const tab of $('taskman-tabs').querySelectorAll('[data-tab]')) { const on = tab.dataset.tab === name; tab.setAttribute('aria-selected', String(on)); tab.tabIndex = on ? 0 : -1; $(tab.getAttribute('aria-controls')).hidden = !on; }
  }

  // Use of the processor and of the memory now (the gauges) and over the last minute (the green lines).
  performance() {
    const data = this.data, history = data?.history || [], share = (used, total) => total ? 100 * used / total : null;
    const cpu = data?.cpu_percent ?? null, memory = share(data?.ram_used_bytes, data?.ram_total_bytes);
    $('perf-cpu-gauge').style.setProperty('--pct', Math.max(0, Math.min(100, cpu ?? 0)) + '%'); $('perf-cpu-value').textContent = cpu == null ? '—' : number(cpu) + ' %';
    $('perf-memory-gauge').style.setProperty('--pct', Math.max(0, Math.min(100, memory ?? 0)) + '%'); $('perf-memory-value').textContent = memory == null ? '—' : number(data.ram_used_bytes / GIB, 1) + ' GiB';
    $('perf-cpu-line').setAttribute('points', sparkPoints(history.map(point => point.cpu_percent), 100, 200, 60));
    $('perf-memory-line').setAttribute('points', sparkPoints(history.map(point => share(point.ram_used_bytes, point.ram_total_bytes)), 100, 200, 60));
  }

  // The illustration matches the family of this computer's device and whether it is working now.
  card(device) {
    if (!device) return;
    this.busy = gpuBusy(this.busy, device.utilization_percent);
    $('system-gpu-caption').textContent = gpuShort(device.name);
    const image = gpuFamily(device.name) + '-' + (this.busy ? 'busy' : 'idle');
    if (image !== this.image) {
      this.image = image;
      for (const theme of ['light', 'dark']) $('system-gpu-image-' + theme).src = `/assets/icons/gpu/${image.replace(/-(idle|busy)$/, '-' + theme + '-$1')}`+'.webp';
    }
    // Each reading has its own slot of fixed width and whole numbers, so nothing moves when a value changes.
    const slot = (id, value, unit) => { $(id).textContent = value == null ? '—' : number(value) + unit; };
    slot('gpu-now-use', device.utilization_percent, ' %'); slot('gpu-now-heat', device.temperature_c, ' °C'); slot('gpu-now-power', device.power_w, ' W');
  }

  // The last minute, from the history the server sends with every reading: use of the device and of its memory.
  chart(data, device) {
    const history = data?.history || [], total = device?.memory_total_bytes;
    $('system-gpu-chart-use').setAttribute('points', sparkPoints(history.map(point => point.gpu_percent)));
    $('system-gpu-chart-vram').setAttribute('points', sparkPoints(history.map(point => point.vram_total_bytes || total ? 100 * point.vram_used_bytes / (point.vram_total_bytes || total) : null)));
    $('system-gpu-history').hidden = history.length < 2;
  }

  readings() {
    const cell = (value, unit, digits = 0) => value == null ? '—' : number(value, digits) + unit;
    $('system-gpu-log').replaceChildren(...this.log.map(row => {
      const item = node('li');
      item.append(node('span', 'gpu__time', clockOf(row.time)), node('span', '', cell(row.use, ' %')), node('span', '', row.vram == null ? '—' : number(row.vram / GIB, 1) + ' GiB'), node('span', '', cell(row.heat, ' °C')), node('span', '', cell(row.power, ' W')));
      return item;
    }));
  }

  processes() {
    const data = this.data, rows = sortProcesses(data?.processes || [], this.sort.key, this.sort.descending);
    for (const control of $('taskman-head').querySelectorAll('[data-sort]')) {
      const on = control.dataset.sort === this.sort.key;
      control.closest('th').setAttribute('aria-sort', on ? this.sort.descending ? 'descending' : 'ascending' : 'none');
    }
    $('hardware-processes').replaceChildren(...rows.map(process => {
      const row = node('tr'); row.dataset.pid = process.pid; row.classList.toggle('is-selected', process.pid === this.selected);
      const name = node('td', '', process.name); name.title = process.name + ' · ' + (process.role === 'rag' ? t('ui.rag_server_1f279b') : 'Ollama');
      row.append(name, node('td', '', process.role === 'rag' ? 'RAG' : 'Ollama'), node('td', 'num', process.pid),
        node('td', 'num', process.cpu_percent == null ? '—' : String(Math.round(process.cpu_percent)).padStart(2, '0')), node('td', 'num', number(process.ram_bytes / 1024) + ' KB'));
      return row;
    }));
    $('taskman-count').textContent = t('system.taskman_count', {count: rows.length});
    $('taskman-cpu').textContent = data?.cpu_percent == null ? '' : t('system.taskman_cpu', {value: number(data.cpu_percent)});
    $('taskman-memory').textContent = data?.ram_total_bytes ? t('system.taskman_memory', {used: number(data.ram_used_bytes / GIB, 1), total: number(data.ram_total_bytes / GIB, 1)}) : '';
  }
}
