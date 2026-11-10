import {renderMarkdown} from './markdown.js';

export const $ = (id) => document.getElementById(id);
export const number = (value, digits = 0) => new Intl.NumberFormat('es-ES', {minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: 'always'}).format(value);
export const size = (value) => value < 1024 * 1024 ? `${number(value / 1024, 1)} KB` : `${number(value / 1024 / 1024, 1)} MB`;
export const duration = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
export const normalize = (text) => String(text).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
export const terminal = new Set(['indexed', 'unindexed', 'error', 'rejected', 'cancelled', 'interrupted']);
export function node(tag, className = '', text) { const element = document.createElement(tag); if (className) element.className = className; if (text != null) element.textContent = String(text); return element; }
export function icon(name) { const element = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); element.classList.add('icon'); element.setAttribute('aria-hidden', 'true'); const use = document.createElementNS(element.namespaceURI, 'use'); use.setAttribute('href', `#i-${name}`); element.append(use); return element; }
export function button(text, className, action, label) { const element = node('button', className, text); element.type = 'button'; if (label) element.setAttribute('aria-label', label); element.addEventListener('click', action); return element; }
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
    FILE_TOO_LARGE: ['Supera el límite de 20 MB por archivo.', 'Divídelo o comprímelo y añádelo de nuevo.', false],
    UNSUPPORTED_TYPE: ['Formato no compatible.', 'Conviértelo a PDF, TXT o Markdown.', false],
    DUPLICATE: ['Ya está en la biblioteca sin cambios.', 'No hace falta volver a indexarlo.', false],
    NO_TEXT: ['No se ha podido extraer texto: parece un PDF escaneado.', 'Actívale OCR y vuelve a procesarlo.', true],
    OLLAMA_UNAVAILABLE: ['Ollama no responde.', 'Comprueba que Ollama está en marcha y reintenta.', true],
    EMBEDDINGS_NOT_READY: ['El modelo de búsqueda no está preparado.', 'Pulsa «Preparar modelo de búsqueda» en Configuración.', true],
    NETWORK: ['Se perdió la conexión con el servidor local.', 'Comprueba que la app sigue en marcha.', true],
    INVALID_NAME: ['El nombre del archivo no es válido.', 'Renómbralo sin rutas ni caracteres reservados de Windows.', false],
    UNEXPECTED: ['Error inesperado al procesar el archivo.', 'Consulta los detalles técnicos.', true],
  };
  const [message, hint, retryable] = catalog[code] || catalog.UNEXPECTED;
  return {code, message: input.message || message, hint: input.hint || hint, detail: input.detail || '', retryable: input.retryable ?? retryable};
}
export async function copyText(text, message = 'Respuesta copiada') { try { await navigator.clipboard.writeText(text); toast(message); } catch { toast('No se ha podido copiar. Selecciona el texto y usa Ctrl + C.'); } }

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
    const pageLocation = hasPage ? `${pdf ? 'pág. PDF' : 'pág.'} ${page}` : (source.section ? `sección ${source.section}` : source.lines ? `líneas ${source.lines}` : 'Fragmento recuperado');
    const pageLabel = pdf && hasPage && source.page_label && String(source.page_label) !== String(page) ? `numeración del documento: ${source.page_label}` : '';
    const location = [pageLocation, pageLabel, source.chunk != null ? `fragmento ${source.chunk}` : ''].filter(Boolean).join(' · ');
    meta.append(node('span', '', location));
    const score = Math.max(0, Math.min(1, source.relevance ?? (1 - Number(source.cosine_distance || 0))));
    const relevance = node('div', 'relevance'); relevance.title = 'Similitud de coseno recortada al intervalo 0–1; no es una probabilidad.';
    relevance.append(progress(score * 100, score >= .85 ? 'moss' : score >= .75 ? '' : 'ochre', `Relevancia de ${source.file}`), node('span', '', number(score, 2))); meta.append(relevance);
    const excerpt = node('p', 'source-excerpt'); highlighted(excerpt, source.text || '', question);
    card.append(top, meta, excerpt);
    for (const figure of source.figures || []) {
      if (!/^\/api\/figures\/[a-f0-9]{64}$/.test(figure.url)) continue;
      const link = node('a', 'figure-link'); link.href = figure.url; link.target = '_blank'; link.rel = 'noopener';
      const figureLocation = `${pdf ? 'pág. PDF' : 'pág.'} ${figure.page}`;
      const image = node('img', 'source-figure'); image.src = figure.url; image.loading = 'lazy'; image.alt = `Vista documental de ${source.file}, ${figureLocation}`;
      link.append(image, node('span', 'help-text', `Ver imagen · ${figureLocation}`)); card.append(link);
    }
    if (source.url && source.url.startsWith('/api/source?')) {
      const link = node('a', 'source-link', pdf && hasPage ? `Abrir página PDF ${page}` : 'Abrir en el documento');
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
  const failed = failures.length > 0;
  dialog.dataset.variant = failed ? 'error' : 'success'; dialog.setAttribute('role', failed ? 'alertdialog' : 'dialog');
  $('dialog-icon').replaceChildren(icon(failed ? 'alert' : 'check'));
  const title = failed ? (result.kind === 'prepare' ? 'No se ha podido preparar el modelo' : `${failures.length} documento${failures.length === 1 ? ' no se ha indexado' : 's no se han indexado'}`) : result.kind === 'prepare' ? 'Modelo de búsqueda listo' : result.kind === 'index' ? 'Índice actualizado' : result.unindexed ? 'Subida completada' : 'Indexación completada';
  $('dialog-title').textContent = title;
  $('dialog-description').textContent = failed ? (successes.length ? `El resto del lote (${successes.length} documentos) está en tu biblioteca. Revisa estos y vuelve a intentarlo.` : 'No se ha añadido ningún documento de este lote.') : result.kind === 'prepare' ? 'Los embeddings están descargados y cargados en CPU.' : result.kind === 'index' ? `${result.summary?.indexed_files || 0} documentos actualizados · ${result.summary?.skipped_files || 0} sin cambios omitidos.` : result.unindexed ? `${successes.length} documentos se han subido. Puedes indexarlos cuando quieras.` : `${successes.length} documentos nuevos ya forman parte de tu biblioteca. Puedes consultarlos desde ahora.`;
  $('dialog-stats').replaceChildren(); $('dialog-stats').hidden = failed || result.kind === 'prepare';
  if (!failed && result.kind !== 'prepare') for (const [value, label] of [[result.kind === 'index' ? result.summary?.indexed_files || 0 : successes.length, 'documentos'], [successes.reduce((sum, item) => sum + (item.skipped ? 0 : item.chunks || 0), 0), 'fragmentos nuevos'], [duration(result.duration || 0), 'min de proceso']]) { const stat = node('div', 'dialog-stat'); stat.append(node('strong', '', typeof value === 'number' ? number(value) : value), node('span', '', label)); $('dialog-stats').append(stat); }
  $('dialog-list').replaceChildren();
  if (failed) {
    for (const item of failures) {
      const card = node('article', 'dialog-error-card'); card.append(node('strong', '', item.name), node('p', 'error-reason', item.error.message), node('p', 'error-hint', item.error.hint));
      if (item.retry && item.error.retryable && item.error.code !== 'NO_TEXT') card.append(button('Reintentar', 'button error-outline', () => { dialog.close(); item.retry(); }));
      else if (item.remove) card.append(button('Quitar', 'button secondary', () => { dialog.close(); item.remove(); }));
      $('dialog-list').append(card);
    }
  } else {
    for (const item of successes.slice(0, 5)) { const row = node('div', 'dialog-document'); row.append(node('span', '', `✓  ${item.name}`), node('span', 'mono', number(item.chunks || 0))); $('dialog-list').append(row); }
    if (successes.length > 5) $('dialog-list').append(node('p', 'help-text', `y ${successes.length - 5} más`));
  }
  $('dialog-technical').hidden = !failed; $('dialog-technical').open = false;
  const details = failures.map((item) => `${item.name}\n${item.error.code}: ${item.error.detail || item.error.message}`).join('\n\n');
  $('dialog-technical').querySelector('pre').textContent = details;
  $('copy-details').onclick = async () => { try { await navigator.clipboard.writeText(details); toast('Detalles copiados'); } catch { toast('Selecciona los detalles y usa Ctrl + C.'); } };
  $('dialog-actions').replaceChildren();
  const close = button(failed ? 'Cerrar' : 'Seguir añadiendo', 'button secondary', () => dialog.close()); $('dialog-actions').append(close);
  let primary;
  if (failed) { const retryable = failures.filter((item) => item.retry && item.error.retryable); if (retryable.length) { primary = button('Reintentar los fallidos', 'button danger', () => { dialog.close(); if (result.retryAll) result.retryAll(); else retryable.forEach((item) => item.retry()); }); $('dialog-actions').append(primary); } }
  else { primary = button('Hacer una consulta', 'button primary', () => { dialog.close(); window.dispatchEvent(new CustomEvent('rag:navigate', {detail: '/'})); }); primary.append(icon('arrow')); $('dialog-actions').append(primary); }
  document.body.classList.add('modal-open'); dialog.showModal(); (failed ? close : primary || close).focus({preventScroll: true});
}
