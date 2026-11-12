// Tender search: read the platform's listing, look at the results, then download what is chosen.
import {t, intlLocale, serverText} from './i18n.js';
import {$, node, number, normalize, toast, button} from './ui.js';

const MAX_DOWNLOAD = 25;
const PERIODS = [1, 3, 7, 15, 30];
const k = (key, values = {}) => t(`tenders.${key}`, values);
const terminal = new Set(['done', 'error', 'cancelled']);
// Every community can be chosen even when the listing has nothing there yet (names as tenders.REGIONS writes them).
const COMMUNITIES = ['Andalucía', 'Aragón', 'Canarias', 'Cantabria', 'Castilla y León', 'Castilla-La Mancha', 'Cataluña', 'Ceuta',
  'Comunidad de Madrid', 'Comunidad Foral de Navarra', 'Comunitat Valenciana', 'Extremadura', 'Galicia', 'Illes Balears', 'La Rioja',
  'Melilla', 'País Vasco', 'Principado de Asturias', 'Región de Murcia', 'toda España'];
// Every tender is in exactly one of these: accepting bids today; deadline over but not awarded (the
// platform says so, or still says "open" after the deadline); awarded, concluded or cancelled.
const stateOf = row => row.open ? 'open' : ['EV', 'PUB'].includes(row.status) ? 'pending' : 'closed';

export class Tenders {
  constructor(api, refreshStatus) {
    this.api = api; this.refreshStatus = refreshStatus;
    this.data = null; this.profile = null; this.selected = new Set(); this.watched = new Set();
    this.reading = null; this.notice = ''; this.armed = null; this.tab = 'search'; this.state = 'open';
    this.dialog = $('tenders-dialog');
    $('tenders-button').addEventListener('click', () => this.open());
    $('tenders-close').addEventListener('click', () => this.dialog.close());
    this.dialog.addEventListener('close', () => document.body.classList.remove('modal-open'));
    for (const tab of this.dialog.querySelectorAll('.tenders-tab')) tab.addEventListener('click', () => {
      // "To download" starts on what can still be bid for; "in my library" shows everything held.
      this.tab = tab.dataset.tab; this.state = this.tab === 'search' ? 'open' : 'all'; this.selected.clear(); this.armed = null; this.render();
    });
    $('tenders-states').addEventListener('click', event => { const control = event.target.closest('[data-state]'); if (control) { this.state = control.dataset.state; this.render(); } });
    $('tenders-region').addEventListener('change', () => this.render());
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
  regionName(name) { return name === 'toda España' ? k('region_national') : name; }

  // The tab decides the set; text and region narrow it; the state buttons split what is left.
  inTab() { return (this.data?.tenders || []).filter(row => row.in_library === (this.tab === 'library')); }
  matching() {
    const words = normalize($('tenders-search').value).split(/\s+/).filter(Boolean);
    return this.inTab().filter(row => {
      if (!words.length) return true;
      const text = normalize([row.folder, row.title, row.organ, row.place, row.region, ...(row.cpv || [])].join(' '));
      return words.every(word => text.includes(word));
    });
  }
  narrowed() { const region = $('tenders-region').value; return this.matching().filter(row => !region || row.region === region); }
  shown() { return this.narrowed().filter(row => this.state === 'all' || stateOf(row) === this.state); }

  stateLabel(row, status = row.status) {
    if (status === 'PUB') return k(row.open || status !== row.status ? 'status_PUB' : 'status_PUB_closed');
    return ['PRE', 'EV', 'ADJ', 'RES', 'ANUL'].includes(status) ? k(`status_${status}`) : (status || k('status_unknown'));
  }

  render() {
    const data = this.data; if (!data) return;
    const source = data.sources.find(item => item.profile === data.profile) || data.sources[0];
    $('tenders-source').textContent = source ? k('source', {source: source.source, profile: source.profile}) : '';
    const library = data.tenders.filter(row => row.in_library).length;
    for (const tab of this.dialog.querySelectorAll('.tenders-tab')) {
      tab.textContent = k(`tab_${tab.dataset.tab}`, {count: number(tab.dataset.tab === 'library' ? library : data.tenders.length - library)});
      tab.setAttribute('aria-selected', String(tab.dataset.tab === this.tab));
    }
    // Every community, each with how many tenders of this tab and text it has in the chosen state.
    const regions = $('tenders-region'), chosen = regions.value, inState = this.matching().filter(row => this.state === 'all' || stateOf(row) === this.state);
    const perRegion = new Map(); for (const row of inState) perRegion.set(row.region, (perRegion.get(row.region) || 0) + 1);
    const names = [...new Set([...COMMUNITIES, ...perRegion.keys()].filter(Boolean))];
    regions.replaceChildren(new Option(k('region_option', {name: k('region_all'), count: number(inState.length)}), ''),
      ...names.map(name => new Option(k('region_option', {name: this.regionName(name), count: number(perRegion.get(name) || 0)}), name)));
    regions.value = names.includes(chosen) ? chosen : '';
    const narrowed = this.narrowed(), counts = {open: 0, pending: 0, closed: 0, all: narrowed.length};
    for (const row of narrowed) counts[stateOf(row)]++;
    for (const control of $('tenders-states').querySelectorAll('[data-state]')) {
      control.textContent = k(`state_${control.dataset.state}`, {count: number(counts[control.dataset.state])});
      control.setAttribute('aria-pressed', String(control.dataset.state === this.state));
    }
    this.readState();
    const rows = this.shown(), body = $('tenders-rows'); body.replaceChildren();
    for (const row of rows) body.append(this.row(row));
    this.dialog.querySelector('.tenders-table').hidden = !rows.length;
    this.explain(rows.length, narrowed.length);
    const pickable = rows.filter(row => this.selectable(row));
    $('tenders-select-all').checked = pickable.length > 0 && pickable.every(row => this.selected.has(row.key));
    $('tenders-select-all').disabled = !pickable.length;
    this.shownCount = rows.length; this.actions();
  }

  // An empty result always says which choice left it empty and offers the way out.
  explain(shown, narrowed) {
    const box = $('tenders-empty'), text = $('tenders-empty-text'), actions = $('tenders-empty-actions'), data = this.data;
    box.hidden = shown > 0; actions.replaceChildren(); if (shown) return;
    const total = this.inTab().length, library = data.tenders.filter(row => row.in_library).length;
    const from = data.oldest ? this.date(data.oldest) : '—';
    // The next period longer than what the listing already covers.
    const covered = data.oldest ? (Date.now() - Date.parse(data.oldest)) / 86400000 : 0;
    const longer = PERIODS.find(days => days > covered + 0.5) || PERIODS.at(-1);
    const readMore = () => button(k('action_read', {days: longer}), 'btn btn--primary', () => { $('tenders-days').value = String(longer); this.read(); });
    const reading = Boolean(this.reading);
    if (this.tab === 'library') {
      if (!total) { text.textContent = k('empty_library'); return; }
      if (!narrowed) { text.textContent = k('empty_text', {total: number(total)}); actions.append(this.clearButton()); return; }
      text.textContent = k('empty_state_library', {total: number(narrowed), state: k(`state_name_${this.state}`)});
      actions.append(this.allStatesButton(narrowed)); return;
    }
    if (!data.read_at && !total) { text.textContent = k('empty_never'); if (!reading) actions.append(button(k('read'), 'btn btn--primary', () => this.read())); return; }
    if (!total) { text.textContent = k('empty_all_downloaded', {library: number(library), from}); if (!reading) actions.append(readMore()); return; }
    if (!narrowed) { text.textContent = k('empty_text', {total: number(total)}); actions.append(this.clearButton()); if (!reading) actions.append(readMore()); return; }
    text.textContent = k('empty_state', {total: number(narrowed), state: k(`state_name_${this.state}`), from});
    actions.append(this.allStatesButton(narrowed)); if (!reading) actions.append(readMore());
  }
  clearButton() { return button(k('action_clear'), 'btn btn--secondary', () => { $('tenders-search').value = ''; $('tenders-region').value = ''; this.render(); }); }
  allStatesButton(count) { return button(k('action_all_states', {count: number(count)}), 'btn btn--secondary', () => { this.state = 'all'; this.render(); }); }

  row(row) {
    const line = node('tr'), pick = node('td'), box = node('input');
    box.type = 'checkbox'; box.dataset.key = row.key; box.checked = this.selected.has(row.key); box.disabled = !this.selectable(row);
    box.setAttribute('aria-label', k('select_one', {folder: row.folder})); pick.append(box);
    const subject = node('td', 'tender-subject'), details = node('small', '', `${row.folder} · ${row.organ}`);
    subject.append(node('strong', '', row.title), details);
    if (row.link) { const link = node('a', '', k('view')); link.href = row.link; link.target = '_blank'; link.rel = 'noopener noreferrer'; details.append(' · ', link); }
    const state = node('td'), kind = {open: 'badge--ok', pending: 'badge--warn', closed: 'badge--neutral'}[stateOf(row)];
    state.append(node('span', `badge ${kind}`, this.stateLabel(row)));
    if (row.in_library) {
      state.append(node('small', '', k('in_library', {files: row.files})));
      if (row.library_status && row.library_status !== row.status) state.append(node('small', '', k('sheet_outdated', {state: this.stateLabel(row, row.library_status)})));
    }
    const deadline = node('td', 'num', row.deadline ? this.date(row.deadline + 'T00:00:00', {dateStyle: 'short'}) : k('no_deadline'));
    if (row.deadline && row.deadline_time) deadline.append(node('small', '', row.deadline_time));
    const place = node('td', '', row.region ? this.regionName(row.region) : '—'); if (row.place && row.place !== row.region) place.append(node('small', '', row.place));
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
    const library = data.tenders.filter(row => row.in_library).length;
    text.textContent = data.read_at
      ? k('read_state', {date: this.date(data.read_at, {dateStyle: 'short', timeStyle: 'short'}), from: this.date(data.oldest), new: number(data.tenders.length - library), library: number(library)})
      : k('never_read');
  }

  actions() {
    const rows = new Map((this.data?.tenders || []).map(row => [row.key, row])), chosen = [...this.selected].map(key => rows.get(key)).filter(Boolean);
    this.toDownload = chosen.filter(row => !row.in_library && row.technical > 0).map(row => row.key);
    this.toWithdraw = chosen.filter(row => row.in_library).map(row => row.key);
    const busy = Boolean(this.downloading);
    $('tenders-download-label').textContent = k('download', {count: this.toDownload.length});
    $('tenders-download').disabled = busy || !this.toDownload.length; $('tenders-download').hidden = this.tab !== 'search';
    if (this.armed && this.armed.join() !== this.toWithdraw.join()) this.armed = null;
    $('tenders-withdraw-label').textContent = k(this.armed ? 'withdraw_confirm' : 'withdraw', {count: this.toWithdraw.length});
    $('tenders-withdraw').disabled = busy || !this.toWithdraw.length; $('tenders-withdraw').hidden = this.tab !== 'library';
    $('tenders-count').textContent = k('count', {shown: number(this.shownCount || 0), selected: number(chosen.length)});
  }

  post(path, body) { return this.api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); }

  async read() {
    this.fail(null); this.say('');
    const days = Number($('tenders-days').value);
    try { const {job_id} = await this.post('/api/admin/tenders/refresh', {profile: this.profile, days}); this.watch(job_id); }
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
      const first = !this.reading;
      this.reading = {id: job.id, state, progress: job.status === 'queued' ? null : job.progress};
      // The listing is saved as it grows: show what has been found so far.
      if (polls % 6 === 0) this.load().catch(() => {}); else if (this.dialog.open) { if (first) this.render(); else this.readState(); }
      return;
    }
    this.reading = null;
    if (job.status === 'done') this.say(k('reading_done', {scanned: number(state.scanned || 0), found: number(state.found || 0), renewed: number(state.renewed || 0)}));
    else if (job.status === 'cancelled') this.say(k('reading_stopped'));
    else { this.say(k('reading_failed')); this.fail(new Error(job.error_info?.message || job.error || '')); }
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
