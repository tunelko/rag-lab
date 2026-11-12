// Tender search: read the platform's listing, look at the results, then download what is chosen.
import {t, intlLocale, serverText} from './i18n.js';
import {$, node, number, normalize, toast} from './ui.js';

const MAX_DOWNLOAD = 25;
const k = (key, values = {}) => t(`tenders.${key}`, values);
const terminal = new Set(['done', 'error', 'cancelled']);

export class Tenders {
  constructor(api, refreshStatus) {
    this.api = api; this.refreshStatus = refreshStatus;
    this.data = null; this.profile = null; this.selected = new Set(); this.watched = new Set();
    this.reading = null; this.notice = ''; this.armed = null;
    this.dialog = $('tenders-dialog');
    $('tenders-button').addEventListener('click', () => this.open());
    $('tenders-close').addEventListener('click', () => this.dialog.close());
    this.dialog.addEventListener('close', () => document.body.classList.remove('modal-open'));
    for (const id of ['tenders-state', 'tenders-region', 'tenders-show']) $(id).addEventListener('change', () => {
      // What is in the library is listed whatever its state: that is how resolved tenders are found and withdrawn.
      if (id === 'tenders-show' && $('tenders-show').value === 'library') $('tenders-state').value = 'all';
      this.render();
    });
    let timer; $('tenders-search').addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => this.render(), 150); });
    $('tenders-select-all').addEventListener('change', event => {
      for (const row of this.shown()) if (this.selectable(row)) this.selected[event.target.checked ? 'add' : 'delete'](row.key);
      this.render();
    });
    $('tenders-rows').addEventListener('change', event => {
      const box = event.target.closest('input[data-key]'); if (!box) return;
      this.selected[box.checked ? 'add' : 'delete'](box.dataset.key); this.actions();
    });
    $('tenders-refresh').addEventListener('click', () => this.read());
    $('tenders-cancel-reading').addEventListener('click', () => this.reading && this.api(`/api/jobs/${this.reading.id}`, {method: 'DELETE'}).catch(error => this.fail(error)));
    $('tenders-download').addEventListener('click', () => this.download());
    $('tenders-withdraw').addEventListener('click', () => this.withdraw());
    window.addEventListener('rag:languagechange', () => { if (this.data) this.render(); });
    this.load().catch(() => {});
  }

  async load() {
    const data = await this.api('/api/tenders' + (this.profile ? `?profile=${encodeURIComponent(this.profile)}` : ''));
    this.data = data; this.profile = data.profile || null;
    $('tenders-button').hidden = !data.sources.length;
    const known = new Set(data.tenders.map(row => row.key));
    for (const key of [...this.selected]) if (!known.has(key)) this.selected.delete(key);
    for (const job of data.jobs || []) this.watch(job.id);
    if (this.dialog.open) this.render();
    return data;
  }

  open() {
    this.fail(null); document.body.classList.add('modal-open'); this.dialog.showModal();
    if (this.data) this.render();
    this.load().catch(error => this.fail(error));
    $('tenders-search').focus({preventScroll: true});
  }

  fail(error) { const box = $('tenders-error'); box.hidden = !error; box.textContent = error ? (serverText(error.message) || String(error)) : ''; }
  say(text) { this.notice = text || ''; const box = $('tenders-job'); box.hidden = !this.notice; box.textContent = this.notice; }
  selectable(row) { return row.in_library || row.technical > 0; }
  date(value, options = {dateStyle: 'short'}) { const moment = new Date(value); return Number.isNaN(moment.getTime()) ? '—' : moment.toLocaleString(intlLocale(), options); }

  shown() {
    const rows = this.data?.tenders || [], words = normalize($('tenders-search').value).split(/\s+/).filter(Boolean);
    const state = $('tenders-state').value, region = $('tenders-region').value, show = $('tenders-show').value;
    return rows.filter(row => {
      if (state === 'open' && !row.open) return false;
      if (state === 'pending' && row.status !== 'EV') return false;
      if (state === 'unresolved' && !row.open && row.status !== 'EV') return false;
      if (region && row.region !== region) return false;
      if (show === 'new' && row.in_library) return false;
      if (show === 'library' && !row.in_library) return false;
      if (!words.length) return true;
      const text = normalize([row.folder, row.title, row.organ, row.place, row.region, ...(row.cpv || [])].join(' '));
      return words.every(word => text.includes(word));
    });
  }

  stateLabel(row, status = row.status) {
    if (status === 'PUB') return k(row.open || status !== row.status ? 'status_PUB' : 'status_PUB_closed');
    return ['PRE', 'EV', 'ADJ', 'RES', 'ANUL'].includes(status) ? k(`status_${status}`) : (status || k('status_unknown'));
  }

  render() {
    const data = this.data; if (!data) return;
    const source = data.sources.find(item => item.profile === data.profile) || data.sources[0];
    $('tenders-source').textContent = source ? k('source', {source: source.source, profile: source.profile}) : '';
    // Regions of the listing; the chosen one is kept while it still exists.
    const regions = $('tenders-region'), chosen = regions.value;
    regions.replaceChildren(new Option(k('region_all'), ''), ...(data.regions || []).map(name => new Option(name, name)));
    regions.value = (data.regions || []).includes(chosen) ? chosen : '';
    this.readState();
    const rows = this.shown(), body = $('tenders-rows'); body.replaceChildren();
    for (const row of rows) body.append(this.row(row));
    const empty = $('tenders-empty'); empty.hidden = rows.length > 0;
    empty.textContent = k(data.tenders.length ? 'empty_filtered' : 'empty_unread');
    const pickable = rows.filter(row => this.selectable(row));
    $('tenders-select-all').checked = pickable.length > 0 && pickable.every(row => this.selected.has(row.key));
    $('tenders-select-all').disabled = !pickable.length;
    this.shownCount = rows.length; this.actions();
  }

  row(row) {
    const line = node('tr', row.in_library ? 'in-library' : ''), pick = node('td'), box = node('input');
    box.type = 'checkbox'; box.dataset.key = row.key; box.checked = this.selected.has(row.key); box.disabled = !this.selectable(row);
    box.setAttribute('aria-label', k('select_one', {folder: row.folder})); pick.append(box);
    const subject = node('td', 'tender-subject'), details = node('small', '', `${row.folder} · ${row.organ}`);
    subject.append(node('strong', '', row.title), details);
    if (row.link) { const link = node('a', '', k('view')); link.href = row.link; link.target = '_blank'; link.rel = 'noopener noreferrer'; details.append(' · ', link); }
    const state = node('td'), kind = row.open ? 'badge--ok' : row.status === 'EV' ? 'badge--warn' : 'badge--neutral';
    state.append(node('span', `badge ${kind}`, this.stateLabel(row)));
    if (row.in_library) {
      state.append(node('small', '', k('in_library', {files: row.files})));
      if (row.library_status && row.library_status !== row.status) state.append(node('small', '', k('sheet_outdated', {state: this.stateLabel(row, row.library_status)})));
    }
    const deadline = node('td', 'num', row.deadline ? this.date(row.deadline + 'T00:00:00', {dateStyle: 'short'}) : k('no_deadline'));
    if (row.deadline && row.deadline_time) deadline.append(node('small', '', row.deadline_time));
    const place = node('td', '', row.region || '—'); if (row.place && row.place !== row.region) place.append(node('small', '', row.place));
    const amount = node('td', 'num', row.amount && Number.isFinite(Number(row.amount)) ? number(Number(row.amount), 0) + ' €' : '—');
    const documents = node('td', '', k(row.technical ? (row.legal ? 'documents_both' : 'documents_technical') : 'documents_none'));
    documents.title = row.selected_by || '';
    line.append(pick, subject, state, deadline, place, amount, documents);
    return line;
  }

  readState() {
    const data = this.data, reading = this.reading, text = $('tenders-read-state'), bar = $('tenders-reading-progress');
    $('tenders-refresh').hidden = Boolean(reading); $('tenders-days').disabled = Boolean(reading); $('tenders-cancel-reading').hidden = !reading; bar.hidden = !reading;
    if (reading) {
      const state = reading.state || {};
      text.textContent = k('reading', {pages: number(state.pages || 0), scanned: number(state.scanned || 0), found: number(state.found || 0)});
      if (reading.progress == null) bar.removeAttribute('value'); else bar.value = reading.progress;
      return;
    }
    text.textContent = data.read_at
      ? k('read_state', {date: this.date(data.read_at, {dateStyle: 'short', timeStyle: 'short'}), from: this.date(data.oldest, {dateStyle: 'short', timeStyle: 'short'}), count: number(data.tenders.length)})
      : k('never_read');
  }

  actions() {
    const rows = new Map((this.data?.tenders || []).map(row => [row.key, row])), chosen = [...this.selected].map(key => rows.get(key)).filter(Boolean);
    this.toDownload = chosen.filter(row => !row.in_library && row.technical > 0).map(row => row.key);
    this.toWithdraw = chosen.filter(row => row.in_library).map(row => row.key);
    const busy = Boolean(this.downloading);
    $('tenders-download-label').textContent = k('download', {count: this.toDownload.length});
    $('tenders-download').disabled = busy || !this.toDownload.length;
    if (this.armed && this.armed.join() !== this.toWithdraw.join()) this.armed = null;
    $('tenders-withdraw-label').textContent = k(this.armed ? 'withdraw_confirm' : 'withdraw', {count: this.toWithdraw.length});
    $('tenders-withdraw').disabled = busy || !this.toWithdraw.length; $('tenders-withdraw').hidden = !(this.data?.tenders || []).some(row => row.in_library);
    $('tenders-count').textContent = k('count', {shown: number(this.shownCount || 0), total: number(this.data?.tenders.length || 0), selected: number(chosen.length)});
  }

  post(path, body) { return this.api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); }

  async read() {
    this.fail(null); this.say('');
    try { const {job_id} = await this.post('/api/admin/tenders/refresh', {profile: this.profile, days: Number($('tenders-days').value)}); this.watch(job_id); }
    catch (error) { this.fail(error); }
  }

  async download() {
    this.fail(null);
    if (this.toDownload.length > MAX_DOWNLOAD) { this.fail(new Error(k('download_limit', {max: MAX_DOWNLOAD}))); return; }
    try { const {job_id} = await this.post('/api/admin/tenders/download', {profile: this.profile, keys: this.toDownload}); this.watch(job_id); }
    catch (error) { this.fail(error); }
  }

  async withdraw() {
    this.fail(null);
    // The first press asks; the second, on the same selection, moves the documents to a backup folder.
    if (!this.armed) { this.armed = [...this.toWithdraw]; this.actions(); setTimeout(() => { this.armed = null; this.actions(); }, 6000); return; }
    const keys = this.armed; this.armed = null; $('tenders-withdraw').disabled = true;
    try {
      const result = await this.post('/api/admin/tenders/withdraw', {confirm: 'withdraw-tenders', keys});
      for (const key of keys) this.selected.delete(key);
      this.say(k('withdrawn', {tenders: number(result.tenders), documents: number(result.documents), path: result.backup}));
      await Promise.all([this.load(), this.refreshStatus()]);
    } catch (error) { this.fail(error); this.actions(); }
  }

  // Follow a reading or a download until it ends; a reloaded page picks its jobs up again from the listing.
  async watch(id) {
    if (this.watched.has(id)) return; this.watched.add(id);
    let polls = 0;
    try {
      while (true) {
        const job = await this.api(`/api/jobs/${id}`);
        if (job.kind === 'tenders_refresh') this.followReading(job, ++polls); else this.followDownload(job);
        if (terminal.has(job.status)) break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    } catch (error) { this.reading = null; this.downloading = null; this.fail(error); }
    finally { this.watched.delete(id); await this.load().catch(() => {}); if (this.dialog.open) this.render(); }
  }

  followReading(job, polls) {
    const state = job.tender_reading || {};
    if (!terminal.has(job.status)) {
      this.reading = {id: job.id, state, progress: job.status === 'queued' ? null : job.progress};
      // The listing is saved as it grows: show what has been found so far.
      if (polls % 6 === 0) this.load().catch(() => {}); else if (this.dialog.open) this.readState();
      return;
    }
    this.reading = null;
    if (job.status === 'done') this.say(k('reading_done', {scanned: number(state.scanned || 0), found: number(state.found || 0), renewed: number(state.renewed || 0)}));
    else if (job.status === 'cancelled') this.say(k('reading_stopped'));
    else this.fail(new Error(job.error_info?.message || job.error || ''));
    if (state.renewed) this.refreshStatus();
  }

  followDownload(job) {
    if (!terminal.has(job.status)) {
      this.downloading = job.id;
      const percent = job.stage === 'download' ? '' : job.progress != null ? ` · ${number(job.progress)} %` : '';
      this.say(`${serverText(job.phase)}${job.current_document && job.stage !== 'download' ? ' · ' + job.current_document : ''}${percent}`);
      if (this.dialog.open) this.actions();
      return;
    }
    this.downloading = null; this.refreshStatus();
    if (job.status === 'cancelled') { this.say(k('job_cancelled')); return; }
    const outcomes = job.tender_outcomes || [], done = outcomes.filter(item => item.downloaded), failed = outcomes.filter(item => !item.downloaded);
    if (job.status === 'error' && !done.length) { this.say(''); this.fail(new Error(job.error_info?.message || job.error || '')); return; }
    for (const item of done) this.selected.delete(item.key);
    const documents = done.reduce((sum, item) => sum + (item.files || 0), 0), unreadable = (job.documents || []).filter(item => item.error_info).length;
    let text = k(failed.length ? 'downloaded_partial' : 'downloaded', {tenders: number(done.length), documents: number(documents), failed: failed.map(item => item.key).join(', ')});
    if (unreadable) text += ' ' + k('unreadable', {count: number(unreadable)});
    this.say(text); if (!this.dialog.open) toast(text);
  }
}
