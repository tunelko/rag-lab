import {t, intlLocale, serverText} from './i18n.js';
import {renderMarkdown} from './markdown.js';

export const $ = (id) => document.getElementById(id);
export const number = (value, digits = 0) => new Intl.NumberFormat(intlLocale(), {minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: 'always'}).format(value);
export const size = (value) => value < 1024 * 1024 ? `${number(value / 1024, 1)} KB` : `${number(value / 1024 / 1024, 1)} MB`;
export const duration = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
export const normalize = (text) => String(text).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
export const terminal = new Set(['indexed', 'unindexed', 'error', 'rejected', 'cancelled', 'interrupted']);
export function node(tag, className = '', text) { const element = document.createElement(tag); if (className) element.className = className; if (text != null) element.textContent = String(text); return element; }
export function icon(name) { const element = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); element.classList.add('icon'); element.setAttribute('aria-hidden', 'true'); const use = document.createElementNS(element.namespaceURI, 'use'); use.setAttribute('href', `#i-${name}`); element.append(use); return element; }
export function button(text, className, action, label) { const element = node('button', className, text); element.classList.add('btn'); if(!className.includes('btn--'))element.classList.add(/primary/.test(className)?'btn--primary':/danger|error-outline/.test(className)?'btn--danger':/text-button/.test(className)?'btn--ghost':'btn--secondary');if(className.includes('icon-button'))element.classList.add('btn--icon');element.type = 'button'; if (label) element.setAttribute('aria-label', label); element.addEventListener('click', action); return element; }
export function iconButton(name, label, action) { const element = button('', 'icon-button', action, label); element.append(icon(name)); return element; }
export function fileType(name) { const type = name.split('.').pop().toLowerCase(); return node('span', `file-type ${type}`, type === 'markdown' ? 'MD' : type.toUpperCase()); }
export function progress(value, color, label) { const element = node('progress', color); element.max = 100; element.value = Math.max(0, Math.min(100, value)); element.setAttribute('role', 'progressbar'); element.setAttribute('aria-valuemin', '0'); element.setAttribute('aria-valuemax', '100'); element.setAttribute('aria-valuenow', String(Math.round(element.value))); element.setAttribute('aria-label', label); return element; }
export function segments(element, values) { element.querySelectorAll('.segment').forEach((segment, index) => { segment.style.width = `${Math.max(0, Math.min(100, values[index] || 0))}%`; }); }
let toastTimer;
export function toast(message, action = null) { clearTimeout(toastTimer); const element = $('toast'); element.querySelector('span').textContent = message; const control = element.querySelector('button'); control.hidden = !action; control.onclick = () => { element.hidden = true; action?.(); }; element.hidden = false; if (!action) toastTimer = setTimeout(() => { element.hidden = true; }, 3000); }
export function announce(message, region = 'queue-announcements') { $(region).textContent = ''; setTimeout(() => { $(region).textContent = message; }, 30); }
export function errorData(input = {}) {
  const code = input.code || 'UNEXPECTED';
  const catalog = {
    FILE_TOO_LARGE: [t("ui.exceeds_the_20_mb_file_limit_c35db2"), t("ui.split_or_compress_it_and_add_it_again_51855e"), false],
    UNSUPPORTED_TYPE: [t("ui.unsupported_format_8fbb1c"), t("ui.convert_it_to_pdf_txt_or_markdown_5ea27e"), false],
    DUPLICATE: [t("ui.already_in_the_library_and_unchanged_a8ff24"), t("ui.it_does_not_need_indexing_again_c2b35b"), false],
    NO_TEXT: [t("ui.could_not_extract_text_this_appears_to_be_a_scanned_pdf_8e3b9c"), t("ui.enable_ocr_and_process_it_again_26f25f"), true],
    OLLAMA_UNAVAILABLE: [t("ui.ollama_is_not_responding_8d0b59"), t("ui.check_that_ollama_is_running_and_retry_d6f79a"), true],
    EMPTY_RESPONSE: [t('sentinel.empty_response'), t('sentinel.empty_response_hint'), true],
    EMBEDDINGS_NOT_READY: [t("ui.the_retrieval_model_is_not_ready_6ccfb4"), t("ui.click_prepare_retrieval_model_in_settings_3aa926"), true],
    NETWORK: [t("ui.the_connection_to_the_local_server_was_lost_8caf59"), t("ui.check_that_the_application_is_still_running_de097f"), true],
    INVALID_NAME: [t("ui.invalid_filename_ccd43a"), t("ui.rename_it_without_paths_or_windows_reserved_characters_89dba1"), false],
    UNEXPECTED: [t("ui.unexpected_error_while_processing_the_file_a145bc"), t("ui.see_the_technical_details_b376ef"), true],
  };
  const [message, hint, retryable] = catalog[code] || catalog.UNEXPECTED;
  return {code, message: serverText(input.message) || message, hint: serverText(input.hint) || hint, detail: input.detail || '', retryable: input.retryable ?? retryable};
}
export async function copyText(text, message = t("ui.answer_copied_41e1be")) { try { await navigator.clipboard.writeText(text); toast(message); } catch { toast(t("ui.could_not_copy_select_the_text_and_use_ctrl_c_687bf3")); } }

export function renderText(container, text, citations = new Set(), prefix = 'src') {
  renderMarkdown(container,text,citations,prefix);
}
export function highlighted(container, text, question) {
  const terms = new Set((normalize(question).match(/[\p{L}\p{N}]{4,}/gu) || []));
  let previous = 0;
  for (const match of String(text).matchAll(/[\p{L}\p{N}]+/gu)) {
    container.append(document.createTextNode(text.slice(previous, match.index)));
    const term = normalize(match[0]);
    container.append([...terms].some((needle) => term.includes(needle)) ? node('mark', '', match[0]) : document.createTextNode(match[0]));
    previous = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(previous)));
}
export function renderSources(sources, question, container = $('sources'), prefix = 'src') {
  container.replaceChildren();
  for (const source of sources) {
    const card = node('article', 'source'); card.id = `${prefix}-${source.citation}`;
    const top = node('div', 'source-top'); top.append(node('span', 'source-num', source.citation), node('strong', 'source-title', source.file), fileType(source.file));
    const meta = node('div', 'source-meta');
    const pdf = /\.pdf$/i.test(source.file);
    const page = Number(source.page);
    const hasPage = Number.isSafeInteger(page) && page > 0;
    const pageLocation = hasPage ? `${pdf ? t("ui.pdf_p_354326") : t("ui.p_96d288")} ${page}` : (source.section ? t("ui.section_4ab0fc", {value1: (source.section)}) : source.lines ? t("ui.lines_895473", {value1: (source.lines)}) : t("ui.retrieved_chunk_b27719"));
    const pageLabel = pdf && hasPage && source.page_label && String(source.page_label) !== String(page) ? t("ui.document_numbering_9d763c", {value1: (source.page_label)}) : '';
    const location = [pageLocation, pageLabel, source.chunk != null ? `fragmento ${source.chunk}` : ''].filter(Boolean).join(' · ');
    meta.append(node('span', '', location));
    const score = Math.max(0, Math.min(1, source.relevance ?? (1 - Number(source.cosine_distance || 0))));
    const relevance = node('div', 'relevance'); relevance.title = t("ui.cosine_similarity_clipped_to_0_1_this_is_not_a_probability_ebe451");
    relevance.append(progress(score * 100, score >= .85 ? 'moss' : score >= .75 ? '' : 'ochre', t("ui.relevance_of_e74d90", {value1: (source.file)})), node('span', '', number(score, 2))); meta.append(relevance);
    const excerpt = node('p', 'source-excerpt'); highlighted(excerpt, source.text || '', question);
    const kind=node('span','badge '+(source.figures?.length?'badge--info':'badge--neutral'),t(source.figures?.length?'sentinel.image':'sentinel.text'));
    top.append(kind);card.append(top, meta, excerpt);
    if((source.text||'').length>160){const expand=button(t('sentinel.expand_source'),'source-expand',()=>{const open=card.classList.toggle('is-expanded');expand.textContent=t(open?'sentinel.collapse_source':'sentinel.expand_source');expand.setAttribute('aria-expanded',String(open));});expand.setAttribute('aria-expanded','false');card.append(expand);}
    for (const figure of source.figures || []) {
      if (!/^\/api\/figures\/[a-f0-9]{64}$/.test(figure.url)) continue;
      const link = node('a', 'figure-link'); link.href = figure.url; link.target = '_blank'; link.rel = 'noopener';
      const figureLocation = `${pdf ? t("ui.pdf_p_354326") : t("ui.p_96d288")} ${figure.page}`;
      const image = node('img', 'source-figure'); image.src = figure.url; image.loading = 'lazy'; image.alt = t("ui.document_view_of_cb6081", {value1: (source.file), value2: (figureLocation)});
      link.append(image, node('span', 'help-text', t("ui.view_image_82ab3f", {value1: (figureLocation)}))); card.append(link);
    }
    if (source.url && source.url.startsWith('/api/source?')) {
      const link = node('a', 'source-link', pdf && hasPage ? t("ui.open_pdf_page_95085f", {value1: (page)}) : t("ui.open_in_document_73e4f6"));
      // Also repair page links in conversations saved by earlier UI versions.
      link.href = pdf && hasPage ? `${source.url.split('#')[0]}#page=${page}` : source.url;
      link.target = '_blank'; link.rel = 'noopener'; link.append(icon('external')); card.append(link);
    }
    container.append(card);
  }
}

let returnFocus;
const dialog = $('result-dialog');
dialog.addEventListener('close', () => { document.body.classList.remove('modal-open'); if (returnFocus?.isConnected && !returnFocus.disabled && returnFocus.getClientRects().length) returnFocus.focus({preventScroll: true}); else document.querySelector('.nav-item[aria-current=page]')?.focus({preventScroll: true}); });
$('dialog-x').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', (event) => { const rect = dialog.getBoundingClientRect(); if (dialog.dataset.variant !== 'error' && event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close(); });
dialog.addEventListener('keydown', (event) => { if (event.key !== 'Tab') return; const controls = [...dialog.querySelectorAll('button:not(:disabled),a[href],summary')].filter((control) => control.getClientRects().length); if (!controls.length) return; if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1).focus(); } else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0].focus(); } });

export function showResult(result, opener = document.activeElement) {
  if (dialog.open) dialog.close();
  returnFocus = opener;
  const failures = (result.failures || []).map((item) => ({...item, error: errorData(item.error)}));
  const successes = result.successes || [];
  const indexed=successes.filter(item=>!item.skipped&&!item.recovered),unchanged=successes.filter(item=>item.skipped),recovered=successes.filter(item=>item.recovered&&!item.skipped);
  const failed = failures.length > 0;
  dialog.dataset.variant = failed ? 'error' : 'success'; dialog.setAttribute('role', failed ? 'alertdialog' : 'dialog');
  $('dialog-icon').replaceChildren(icon(failed ? 'alert' : 'check'));
  const title = failed ? (result.kind === 'prepare' ? t("ui.could_not_prepare_the_model_e1daf6") : t(failures.length === 1 ? 'dialog.failed_one' : 'dialog.failed_other', {count: failures.length})) : result.kind === 'prepare' ? t("ui.retrieval_model_ready_26e48b") : result.kind === 'index' ? t("ui.index_updated_7684ca") : result.unindexed ? t("ui.upload_complete_410ab1") : t("ui.indexing_complete_bc36d9");
  $('dialog-title').textContent = title;
  $('dialog-description').textContent = failed ? (successes.length ? t("ui.the_rest_of_the_batch_documents_is_in_your_library_review_these_f_46910a", {value1: (successes.length)}) : t("ui.no_documents_from_this_batch_were_added_7a3c2e")) : result.kind === 'prepare' ? t("ui.the_embeddings_have_been_downloaded_and_loaded_on_the_cpu_0b89ac") : result.kind === 'index' ? t("ui.documents_updated_unchanged_files_skipped_7d3c83", {value1: (result.summary?.indexed_files || 0), value2: (result.summary?.skipped_files || 0)}) : result.unindexed ? t("ui.documents_uploaded_you_can_index_them_at_any_time_b76544", {value1: (successes.length)}) : t("ui.new_documents_are_now_in_your_library_you_can_query_them_immediat_45830e", {value1: (successes.length)});
  if(failed&&result.kind==='index'){$('dialog-title').textContent=t('sentinel.index_failed');if(!successes.length)$('dialog-description').textContent=t('sentinel.index_failed_hint');}
  if(!failed&&result.kind!=='prepare'&&!result.unindexed)$('dialog-description').textContent=t('sentinel.batch_result',{indexed:indexed.length,unchanged:unchanged.length,recovered:recovered.length});
  $('dialog-stats').replaceChildren(); $('dialog-stats').hidden = failed || result.kind === 'prepare';
  const stats=[[result.unindexed?successes.length:indexed.length,t(result.unindexed?'ui.documents_df9fb2':'sentinel.indexed')]];
  if(unchanged.length)stats.push([unchanged.length,t('sentinel.unchanged')]);if(recovered.length)stats.push([recovered.length,t('sentinel.recovered')]);
  stats.push([indexed.reduce((sum,item)=>sum+(item.chunks||0),0),t('ui.new_chunks_ded5f6')],[duration(result.duration||0),t('ui.processing_min_d0452c')]);
  if (!failed && result.kind !== 'prepare') for (const [value,label] of stats) { const stat=node('div','dialog-stat');stat.append(node('strong','',typeof value==='number'?number(value):value),node('span','',label));$('dialog-stats').append(stat); }
  $('dialog-list').replaceChildren();
  if (failed) {
    for (const item of failures) {
      const card = node('article', 'dialog-error-card'); card.append(node('strong', '', item.name), node('p', 'error-reason', item.error.message), node('p', 'error-hint', item.error.hint));
      if (item.retry && item.error.retryable && item.error.code !== 'NO_TEXT') card.append(button(t("ui.retry_a9254c"), 'button error-outline', () => { dialog.close(); item.retry(); }));
      else if (item.remove) card.append(button(t("ui.remove_f039f4"), 'button secondary', () => { dialog.close(); item.remove(); }));
      $('dialog-list').append(card);
    }
  } else {
    for (const item of successes.slice(0, 5)) { const row = node('div', 'dialog-document'),name=node('span');name.append(icon('check'),document.createTextNode(item.name));row.append(name,node('span','mono',item.skipped?t('sentinel.unchanged'):number(item.chunks||0)));$('dialog-list').append(row); }
    if (successes.length > 5) $('dialog-list').append(node('p', 'help-text', t("ui.and_more_704e3a", {value1: (successes.length - 5)})));
  }
  $('dialog-technical').hidden = !failed; $('dialog-technical').open = false;
  const details = failures.map((item) => `${item.name}\n${item.error.code}: ${item.error.detail || item.error.message}`).join('\n\n');
  $('dialog-technical').querySelector('pre').textContent = details;
  $('copy-details').onclick = async () => { try { await navigator.clipboard.writeText(details); toast(t("ui.details_copied_ab487a")); } catch { toast(t("ui.select_the_details_and_use_ctrl_c_31bdce")); } };
  $('dialog-actions').replaceChildren();
  const close = button(failed ? t("ui.close_aeccae") : t("ui.add_more_1fe414"), 'button secondary', () => dialog.close()); $('dialog-actions').append(close);
  let primary;
  if (failed) { const retryable = failures.filter((item) => item.retry && item.error.retryable); if (retryable.length) { primary = button(t("ui.retry_failed_files_62b137"), 'button danger', () => { dialog.close(); if (result.retryAll) result.retryAll(); else retryable.forEach((item) => item.retry()); }); $('dialog-actions').append(primary); } }
  else { primary = button(t("ui.ask_a_question_c3cefd"), 'button primary', () => { dialog.close(); window.dispatchEvent(new CustomEvent('rag:navigate', {detail: '/'})); }); primary.append(icon('arrow')); $('dialog-actions').append(primary); }
  document.body.classList.add('modal-open'); dialog.showModal(); (failed ? close : primary || close).focus({preventScroll: true});
}
