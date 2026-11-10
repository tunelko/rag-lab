import {$, node, button, iconButton, renderText, renderSources, copyText, toast, announce} from './ui.js';

const STORE = 'rag-local-chat-v3';
export class Chat {
  constructor(api, changed) {
    this.api = api; this.changed = changed; this.turns = []; this.active = null; this.submitting = false; this.stopping = false;
    try { this.turns = JSON.parse(sessionStorage.getItem(STORE) || '[]').slice(-30); } catch { sessionStorage.removeItem(STORE); }
    this.elements = new Map(); this.render();
    $('question').addEventListener('input', () => this.controls());
    $('question').addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !$('ask-button').disabled) { event.preventDefault(); $('query-form').requestSubmit(); } });
    $('query-form').addEventListener('submit', (event) => { event.preventDefault(); this.send(); });
    $('new-chat').addEventListener('click', () => { if (this.active || this.submitting) return; this.turns = []; this.save(); this.render(); $('question').focus(); });
  }
  save() {
    this.turns = this.turns.slice(-30);
    try { sessionStorage.setItem(STORE, JSON.stringify(this.turns)); } catch { toast('No hay espacio para guardar todo el historial en esta pestaña.'); }
  }
  controls() {
    const busy = Boolean(this.active || this.submitting);
    $('ask-button').querySelector('span').textContent = busy ? this.stopping ? 'Deteniendo…' : 'Detener' : 'Enviar';
    $('ask-button').disabled = this.submitting || this.stopping || (!busy && (!this.status?.chunk_count || !$('question').value.trim()));
    $('ask-button').setAttribute('aria-busy', String(busy));
    $('new-chat').disabled = busy;
    // The form's required textarea must not block the stop button on an empty composer.
    $('question').required = !busy;
    $('question-help').textContent = this.status?.chunk_count ? 'Ctrl + Enter para enviar' : 'Añade e indexa documentos en Biblioteca.';
  }
  setStatus(status) { this.status = status; this.controls(); }
  create(turn) {
    const article = node('article', 'chat-turn'); article.dataset.turn = turn.id;
    const question = node('div', 'chat-question', turn.question); article.append(question);
    const reply = node('div', 'chat-reply'); const label = node('p', 'chat-label', 'RAG Local');
    const answer = node('div', 'answer'); const state = node('p', 'chat-state');
    const details = node('details', 'chat-sources'); const summary = node('summary'); const sources = node('div'); details.append(summary, sources);
    const actions = node('div', 'chat-actions'); const copy = iconButton('copy', 'Copiar respuesta', () => copyText(turn.answer || ''));
    const retry = button('Reintentar', 'text-button', () => this.send(turn.question, turn.id)); actions.append(copy, retry);
    reply.append(label, answer, state, details, actions); article.append(reply); $('chat-thread').append(article);
    const elements = {article, answer, state, details, summary, sources, copy, retry, sourceKey: ''}; this.elements.set(turn.id, elements); return elements;
  }
  update(turn) {
    const thread = $('chat-thread'); const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
    const e = this.elements.get(turn.id) || this.create(turn); const sources = turn.sources || [];
    renderText(e.answer, turn.answer || '', new Set(sources.map(source => Number(source.citation))), `turn-${turn.id}`);
    const busy = ['queued', 'running'].includes(turn.status);
    const indexing=this.status?.jobs?.find(job=>job.kind==='index'&&job.status==='running');
    const waiting=indexing?`En espera mientras se indexa la biblioteca${indexing.document_names?.length?` (${indexing.documents?.length||0}/${indexing.document_names.length} documentos procesados)`:''}. La pregunta se atenderá al terminar ese trabajo.`:'En cola…';
    e.state.textContent = turn.status === 'error' ? (turn.error_info?.message || 'No se ha podido completar la consulta.') : turn.status === 'cancelled' ? 'Consulta detenida. El texto parcial puede estar incompleto.' : busy ? turn.status === 'queued' ? waiting : turn.answer ? 'Escribiendo…' : 'Buscando y preparando la respuesta…' : '';
    e.state.hidden = !e.state.textContent; e.details.hidden = !sources.length; e.summary.textContent = `Fuentes consultadas (${sources.length})`;
    const key = JSON.stringify(sources); if (e.sourceKey !== key) { renderSources(sources, turn.question, e.sources, `turn-${turn.id}`); e.sourceKey = key; }
    e.copy.disabled = busy || !turn.answer; e.retry.hidden = !['error', 'cancelled'].includes(turn.status); e.retry.disabled = Boolean(this.active || this.submitting);
    e.article.setAttribute('aria-busy', String(busy));
    if (atBottom) thread.scrollTop = thread.scrollHeight;
  }
  render() {
    $('chat-thread').replaceChildren(); this.elements.clear(); for (const turn of this.turns) this.update(turn);
    $('chat-empty').hidden = Boolean(this.turns.length); this.controls();
  }
  history(before) {
    return this.turns.slice(0, before).filter(turn => turn.status === 'done').slice(-3).flatMap(turn => [
      {role: 'user', content: turn.question.slice(0, 3000)}, {role: 'assistant', content: (turn.answer || '').slice(0, 3000)}]);
  }
  async send(question = $('question').value.trim(), retryId = null) {
    if (this.active) { this.stopping = true; this.controls(); try { await this.api(`/api/jobs/${this.active}`, {method: 'DELETE'}); } catch (error) { toast(error.message); this.stopping = false; this.controls(); } return; }
    if (this.submitting || !question) return;
    this.submitting = true;
    const turn = retryId ? this.turns.find(turn => turn.id === retryId) : {id: crypto.randomUUID(), question};
    if (!retryId) this.turns.push(turn);
    Object.assign(turn, {answer: '', sources: [], status: 'queued', job_id: null});
    this.render(); $('question').value = ''; this.controls();
    try {
      const result = await this.api('/api/query', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question, history: this.history(this.turns.indexOf(turn))})});
      turn.job_id = result.job_id; this.save(); this.submitting = false; await this.follow(turn);
    } catch (error) { this.submitting = false; turn.status = 'error'; turn.error_info = {message: error.message}; this.update(turn); this.save(); this.controls(); }
  }
  async follow(turn) {
    if (this.active) return; this.active = turn.job_id; this.stopping = false; this.controls();
    try { while (true) {
      const job = await this.api(`/api/jobs/${turn.job_id}`); Object.assign(turn, {status: job.status, answer: job.answer || '', sources: job.sources || [], error_info: job.error_info}); this.update(turn);
      if (['done', 'error', 'cancelled'].includes(job.status)) { this.save(); announce(job.status === 'done' ? 'Respuesta lista' : 'Consulta detenida', 'query-announcements'); this.changed(); break; }
      await new Promise(resolve => setTimeout(resolve, 700));
    } } catch (error) { turn.status = 'error'; turn.error_info = {message: error.message}; this.update(turn); this.save(); }
    finally { this.active = null; this.submitting = false; this.stopping = false; this.controls(); for (const item of this.turns) this.update(item); }
  }
  restore(jobs) {
    const job = jobs.find(item => item.kind === 'query');
    if (job && !this.active) {
      let turn = this.turns.find(item => item.job_id === job.id);
      if (!turn) { turn = {id: crypto.randomUUID(), question: job.question || '', job_id: job.id, status: job.status}; this.turns.push(turn); this.render(); }
      this.follow(turn);
    } else if (!job && !this.active) {
      for (const turn of this.turns.filter(item => ['running', 'queued'].includes(item.status))) { turn.status = 'cancelled'; this.update(turn); }
      this.save();
    }
  }
}
