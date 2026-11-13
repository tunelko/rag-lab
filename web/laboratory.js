import {$, node, number, size, toast} from './ui.js';
import {t, serverText, intlLocale} from './i18n.js';
import {activeExperiment, evidenceOutcome, evidenceCoverage} from './laboratory-state.js';

const label = (key, values = {}) => t(`lab.${key}`, values);
const post = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});

export class Laboratory {
  constructor(api, beforeRun, changed) {
    Object.assign(this, {api, beforeRun, changed, active: false, busy: false, revision: 0,
      meta: null, run: null, job: null, validation: null, questionNodes: new Map(), notified: new Set()});
    $('lab-file').addEventListener('change', () => this.readFile());
    $('lab-content').addEventListener('input', () => this.invalidate());
    $('lab-validate').addEventListener('click', () => this.validate());
    $('lab-form').addEventListener('submit', event => {event.preventDefault(); this.start();});
    for (const id of ['lab-cancel', 'lab-banner-cancel', 'empty-lab-cancel']) $(id).addEventListener('click', () => this.cancel());
    $('empty-lab-view').addEventListener('click', () => {
      $('empty-library-dialog').close(); window.dispatchEvent(new CustomEvent('rag:navigate', {detail: '/laboratorio'}));
    });
    $('empty-library-word').addEventListener('input', () => this.emptyGuard());
    $('empty-library-button').addEventListener('click', () => this.emptyGuard());
    window.addEventListener('rag:languagechange', () => this.render(true));
    document.addEventListener('visibilitychange', () => {if (this.active && !document.hidden) this.load();});
  }

  setActive(value) {this.active = value; clearTimeout(this.timer); if (value) this.load();}

  setStatus(status) {
    const previous = this.job;
    this.job = (status.jobs || []).find(job => job.kind === 'evaluation' && activeExperiment(job)) || null;
    this.renderBanner(); this.emptyGuard();
    if (previous && !this.job) this.load();
    else if (this.active && this.job && this.run?.id !== this.job.lab_run_id) this.load();
  }

  invalidate() {
    this.revision++; this.validation = null;
    $('lab-validation').textContent = '';
    this.controls();
  }

  controls() {
    const working = activeExperiment(this.run) || Boolean(this.job);
    const indexed = (this.meta?.corpus.published_chunks || 0) > 0;
    for (const id of ['lab-file', 'lab-name', 'lab-content']) $(id).disabled = this.busy || working;
    $('lab-validate').disabled = this.busy || working || !indexed || !$('lab-content').value.trim();
    $('lab-run').disabled = this.busy || working || !indexed || !this.validation;
    $('lab-form').setAttribute('aria-busy', String(this.busy));
    $('lab-no-index').hidden = indexed || !this.meta;
  }

  formatError(error) {
    const info = error?.data?.lab_error || error?.lab_error;
    if (info) {
      const key = `lab.error_${info.code}`;
      let message = t(key, info);
      if (message === key) message = label('error_unexpected');
      return info.line ? label('error_line', {line: info.line, message}) : message;
    }
    return serverText(error?.message) || label('error_unexpected');
  }

  error(error) {$('lab-error').textContent = this.formatError(error); $('lab-error').hidden = false;}

  async readFile() {
    const file = $('lab-file').files[0];
    if (!file) return;
    this.invalidate(); $('lab-error').hidden = true;
    const revision = this.revision;
    if (file.size > (this.meta?.limits.bytes || 1048576)) return this.error({lab_error: {code: 'size'}});
    try {
      const content = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
      if (revision !== this.revision) return;
      $('lab-content').value = content;
      $('lab-name').value = file.name.slice(0, 80);
    } catch {this.error({lab_error: {code: 'utf8'}});}
    this.controls();
  }

  payload() {
    return {content: $('lab-content').value, name: $('lab-name').value.trim() || label('dataset'),
      ...(this.validation ? {fingerprint: this.validation.fingerprint} : {})};
  }

  async validate() {
    if ($('lab-validate').disabled) return;
    this.busy = true; this.controls(); $('lab-error').hidden = true;
    const revision = this.revision;
    try {
      const validation = await this.api('/api/laboratory/validate', post(this.payload()));
      if (revision !== this.revision) return;
      this.validation = validation;
      $('lab-validation').textContent = label('validated', {total: validation.queries, annotated: validation.annotated});
    } catch (error) {this.validation = null; this.error(error);}
    finally {this.busy = false; this.controls();}
  }

  async start() {
    if ($('lab-run').disabled) return;
    this.busy = true; this.controls(); $('lab-error').hidden = true;
    try {
      await this.beforeRun();
      const result = await this.api('/api/laboratory/runs', post(this.payload()));
      this.run = await this.api(`/api/laboratory/runs/${encodeURIComponent(result.run_id)}`);
      this.questionNodes.clear(); $('lab-question-results').replaceChildren();
      this.render(); this.schedule(); this.changed();
    } catch (error) {this.error(error);}
    finally {this.busy = false; this.controls();}
  }

  async cancel() {
    const id = this.job?.id || (activeExperiment(this.run) ? this.run.job_id : null);
    if (!id) return;
    for (const control of ['lab-cancel', 'lab-banner-cancel', 'empty-lab-cancel']) $(control).disabled = true;
    try {
      const job = await this.api(`/api/jobs/${encodeURIComponent(id)}`, {method: 'DELETE'});
      this.job = activeExperiment(job) ? job : null;
      this.renderBanner(); this.emptyGuard();
      if (!activeExperiment(job)) await this.load();
      this.changed(); if (this.active) this.schedule();
    } catch (error) {toast(this.formatError(error)); this.changed();}
  }

  async load() {
    if (this.loading) return;
    this.loading = true;
    try {
      const meta = await this.api('/api/laboratory');
      const previous = this.run;
      if (this.validation && this.validation.fingerprint !== meta.corpus.fingerprint) this.invalidate();
      this.meta = meta;
      this.run = meta.latest;
      if (previous && activeExperiment(previous) && this.run && !activeExperiment(this.run)
          && !this.active && !this.notified.has(this.run.id)) {
        this.notified.add(this.run.id);
        toast(label('finished'), () => window.dispatchEvent(new CustomEvent('rag:navigate', {detail: '/laboratorio'})));
      }
      this.render();
    } catch (error) {if (this.active) this.error(error);}
    finally {this.loading = false; this.schedule();}
  }

  schedule() {
    clearTimeout(this.timer);
    if (this.active && !document.hidden && activeExperiment(this.run)) this.timer = setTimeout(() => this.load(), 1200);
  }

  emptyGuard() {
    if (!$('empty-library-dialog').open) return;
    const working = Boolean(this.job) || activeExperiment(this.run);
    $('empty-lab-warning').hidden = !working;
    $('empty-lab-cancel').disabled = !working || Boolean(this.job?.cancel_requested);
    if (!$('empty-library-confirm').hasAttribute('aria-busy')) {
      $('empty-library-confirm').disabled = working || $('empty-library-word').value.trim().toUpperCase() !== t('sentinel.empty_word');
    }
  }

  renderBanner() {
    const job = this.job;
    $('lab-job-banner').hidden = !job;
    if (!job) return;
    $('lab-job-label').textContent = job.cancel_requested ? label('cancelling')
      : label('job_progress', {done: job.completed_questions || 0, total: job.question_count || 0,
        phase: serverText(job.phase)});
    $('lab-banner-cancel').disabled = Boolean(job.cancel_requested);
  }

  definition(container, entries) {
    container.replaceChildren();
    for (const [key, value] of entries) container.append(node('dt', '', key), node('dd', '', value));
  }

  render(rebuild = false) {
    this.controls(); this.renderBanner(); this.emptyGuard();
    $('lab-cleanup-warning').hidden = !(this.meta?.cleanup_pending || this.run?.report?.cleanup_pending);
    $('lab-example').textContent = JSON.stringify({id: 'q1', question: label('example_question'),
      relevant: [{file: 'manual.pdf', page: 8}], evidence: ['LGA1700']});
    if (this.meta) {
      const config = (activeExperiment(this.run) && this.run.report?.configuration) || this.meta.configuration;
      const corpus = this.meta.corpus;
      this.definition($('lab-configuration'), [
        [label('fragments'), config.k], [label('budget'), label('characters', {count: number(config.context_chars)})],
        [label('embedding'), corpus.identity.embedding_model],
        [label('reranker'), config.reranker || label('disabled')],
        [label('device'), this.meta.embedding_device.toUpperCase()],
        [label('reranker_device'), config.reranker ? config.rerank_device : label('disabled')],
        [label('library'), label('corpus_size', {documents: number(corpus.documents), chunks: number(corpus.published_chunks)})],
      ]);
      $('lab-estimate').textContent = label('estimate', {size: size(this.meta.snapshot_bytes)});
      $('lab-gpu-note').hidden = !config.reranker || config.rerank_device === 'cpu';
    }
    const run = this.run;
    $('lab-results').hidden = !run;
    if (!run) return;
    const report = run.report || {}, summary = evidenceCoverage(report.summary);
    $('lab-run-state').textContent = label(`status_${run.status}`) + ' · ' + run.name;
    $('lab-score').textContent = summary.annotated ? label('score', {found: summary.found,
      total: summary.annotated, percent: number(summary.percent, 1)}) : label('no_annotations_yet');
    $('lab-denominator').textContent = label('denominator', {completed: summary.completed, total: summary.total,
      unknown: summary.without});
    $('lab-progress').value = summary.total ? 100 * summary.completed / summary.total : 0;
    $('lab-cancel').hidden = !activeExperiment(run);
    $('lab-cancel').disabled = Boolean(this.job?.cancel_requested);
    $('lab-stale').hidden = run.current_corpus !== false;
    $('lab-run-error').hidden = !run.error;
    if (run.error) $('lab-run-error').textContent = this.formatError(run.error);
    const config = report.configuration || {};
    const failures = config.reranker ? (report.queries || []).filter(row => row.retrieved_ids.length && !row.trace.reranked).length : 0;
    $('lab-rerank-warning').hidden = !failures;
    $('lab-rerank-warning').textContent = failures ? label('rerank_warning', {count: failures}) : '';
    this.definition($('lab-provenance'), [[label('created'), new Date(run.created_at).toLocaleString(intlLocale())],
      [label('dataset_hash'), report.dataset_sha256 || '—'], [label('corpus_hash'), report.corpus?.fingerprint || '—'],
      [label('code_hash'), report.code?.sha256 || '—'], [label('fragments'), config.k ?? report.k ?? '—'],
      [label('scope'), report.scope_files?.join(' · ') || '—'],
      [label('budget'), config.context_chars ?? report.context_chars ?? '—'],
      [label('reranker'), config.reranker || label('disabled')], [label('window'), config.rerank_window ?? '—']]);
    const opened = new Set([...this.questionNodes].filter(([, detail]) => detail.open).map(([id]) => id));
    if (rebuild || this.renderedRun !== run.id || this.renderedCorpus !== run.current_corpus) {
      this.questionNodes.clear(); $('lab-question-results').replaceChildren();
    }
    this.renderedRun = run.id; this.renderedCorpus = run.current_corpus;
    for (const row of report.queries || []) {
      if (this.questionNodes.has(row.id)) continue;
      const detail = this.question(row, run.current_corpus !== false);
      detail.open = opened.has(row.id);
      this.questionNodes.set(row.id, detail); $('lab-question-results').append(detail);
    }
  }

  question(row, current) {
    const detail = node('details', 'lab-result'), summary = node('summary'), outcome = evidenceOutcome(row);
    const badge = node('strong', 'lab-outcome', label(`result_${outcome}`)); badge.dataset.outcome = outcome;
    summary.append(node('span', '', row.question), badge); detail.append(summary);
    const body = node('div', 'lab-result-body');
    body.append(node('h3', '', label('references')));
    const references = node('ul');
    for (const reference of row.relevant || []) references.append(node('li', '', reference.file
      ? reference.file + (reference.page ? ' · ' + label('page', {page: reference.page}) : '') : reference.vector_id));
    body.append(references, node('h3', '', label('expected')));
    if (row.evidence?.length) {const expected = node('ul'); for (const text of row.evidence) expected.append(node('li', '', text)); body.append(expected);}
    else body.append(node('p', 'help-text', label('no_evidence')));
    body.append(node('h3', '', label('context')));
    for (const hit of row.context || []) {
      const heading = node('p', 'lab-context-source');
      const text = (hit.file || '') + (hit.page ? ' · ' + label('page', {page: hit.page}) : '');
      if (current && hit.file) {const link = node('a', '', text); link.href = '/api/source?file=' + encodeURIComponent(hit.file) + (hit.page ? '#page=' + hit.page : ''); link.target = '_blank'; link.rel = 'noopener'; heading.append(link);}
      else heading.textContent = text;
      body.append(heading, node('pre', '', hit.text));
    }
    if (!row.context?.length) body.append(node('p', 'help-text', label('no_context')));
    body.append(node('p', 'help-text', label('question_timing', {milliseconds: number(row.latency_ms, 1), characters: number(row.context_chars)})));
    detail.append(body); return detail;
  }
}
