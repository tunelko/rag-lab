"use strict";
const $ = (id) => document.getElementById(id);
const admin = location.pathname === "/admin";
const state = {token: "", status: null, busy: false, files: [], sourcesKey: "", activeJob: null};
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const fmt = (number) => new Intl.NumberFormat("es-ES", {maximumFractionDigits: 1}).format(number);
const bytes = (number) => number < 1024 * 1024 ? `${fmt(number / 1024)} KB` : `${fmt(number / 1024 / 1024)} MB`;

function notice(message, info = false) { $("notice").textContent = message; $("notice").classList.toggle("info", info); $("notice").hidden = !message; }
async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.method && options.method !== "GET") headers["X-RAG-Token"] = state.token;
  const response = await fetch(path, {...options, headers});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail || result));
  return result;
}
function syncButtons() {
  const indexed = Boolean(state.status?.chunk_count);
  const searchOnly = $("search-only").checked;
  $("ask-button").disabled = state.busy || !indexed || !$("question").value.trim() || (!searchOnly && !$("model").value);
  $("ask-button").firstChild.textContent = searchOnly ? "Buscar" : "Preguntar";
  $("model").disabled = state.busy || searchOnly || !state.status?.ollama?.models.length;
  $("upload-button").disabled = state.busy || !state.files.length;
  $("index-button").disabled = state.busy || !state.status?.document_count;
  $("prepare-button").disabled = state.busy;
  $("file-input").disabled = state.busy;
}
function showStatus(status) {
  state.status = status;
  $("sidebar-docs").textContent = `${fmt(status.document_count)} documento${status.document_count === 1 ? "" : "s"}`;
  $("stat-docs").textContent = fmt(status.document_count);
  $("stat-chunks").textContent = fmt(status.chunk_count);
  $("library-count").textContent = status.document_count;
  $("stat-engine").textContent = status.ollama.connected ? "Ollama conectado" : "Sin conexión";
  $("stat-engine-caption").textContent = status.ollama.loaded.length ? "Modelo cargado · listo para consultas" : "El modelo se cargará al consultar";
  $("ollama-label").textContent = status.ollama.connected ? "Ollama disponible" : "Inicia ollama serve";
  $("empty-banner").hidden = status.chunk_count > 0;
  $("config-collection").textContent = status.collection;
  $("footer-collection").textContent = `Colección: ${status.collection}`;
  $("config-embeddings").textContent = status.embedding_model;
  $("config-device").textContent = status.embedding_device.toUpperCase();
  $("config-chunk").textContent = `${status.chunk_size} / ${status.chunk_overlap} caracteres`;
  const models = status.ollama.models;
  const modelSelect = $("model");
  const remembered = modelSelect.value || localStorage.getItem("rag-local-model") || status.ollama.default_model;
  if (JSON.stringify([...modelSelect.options].map((option) => option.value)) !== JSON.stringify(models)) {
    modelSelect.replaceChildren();
    for (const model of models) { const option = document.createElement("option"); option.value = model; option.textContent = model; modelSelect.append(option); }
    if (!models.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No hay modelos locales disponibles"; modelSelect.append(option); }
    modelSelect.value = models.includes(remembered) ? remembered : (status.ollama.default_model || "");
  }
  $("documents-table").replaceChildren();
  for (const doc of status.documents) {
    const row = document.createElement("tr");
    const name = document.createElement("td"); const link = document.createElement("a"); link.href = `/api/source?file=${encodeURIComponent(doc.name)}`; link.textContent = doc.name; link.title = "Descargar documento original"; name.append(link);
    const size = document.createElement("td"); size.textContent = bytes(doc.bytes);
    const indexed = document.createElement("td"); const pill = document.createElement("span"); pill.className = `status-pill${doc.indexed ? "" : " pending"}`; pill.textContent = doc.indexed ? "Indexado" : "Pendiente"; indexed.append(pill);
    const count = document.createElement("td"); count.textContent = doc.indexed ? fmt(doc.chunks) : "—";
    row.append(name, size, indexed, count); $("documents-table").append(row);
  }
  $("library-empty").hidden = status.documents.length > 0;
  syncButtons();
}
async function refreshStatus() {
  try {
    const status = await api("/api/status"); showStatus(status);
    if (status.active_job && !state.busy) await followJob(status.active_job);
  } catch (error) { notice(error.message); }
}
function renderSources(sources) {
  const key = JSON.stringify(sources);
  if (state.sourcesKey === key) return;
  state.sourcesKey = key;
  $("sources").replaceChildren(); $("source-count").textContent = sources.length;
  $("sources-empty").hidden = sources.length > 0;
  for (const source of sources) {
    const card = document.createElement("details"); card.className = "source"; card.id = `source-${source.citation}`; card.open = source.citation === 1;
    const summary = document.createElement("summary"); const num = document.createElement("span"); num.className = "source-num"; num.textContent = source.citation;
    const title = document.createElement("span"); title.className = "source-title"; title.textContent = source.file;
    const detail = document.createElement("span"); detail.className = "source-page"; detail.textContent = `${source.page ? `Página ${source.page} · ` : ""}Distancia: ${fmt(source.cosine_distance)}`; title.append(detail); summary.append(num, title);
    const content = document.createElement("div"); content.className = "source-content"; content.textContent = source.text;
    card.append(summary, content);
    if (source.url) { const link = document.createElement("a"); link.className = "source-original"; link.textContent = "Descargar original ↗"; link.href = source.url; card.append(link); }
    $("sources").append(card);
  }
}
function renderAnswer(text) {
  $("answer").replaceChildren();
  const pattern = /(\[\d+\]|\*\*[^*]+\*\*|`[^`]+`)/g;
  for (const part of text.split(pattern)) {
    if (/^\[\d+\]$/.test(part)) {
      const button = document.createElement("button"); button.type = "button"; button.className = "citation"; button.textContent = part; button.title = `Ir a fuente ${part}`;
      button.addEventListener("click", () => { const target = $(`source-${part.slice(1, -1)}`); if (target) { target.open = true; target.classList.add("highlight"); target.scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest"}); setTimeout(() => target.classList.remove("highlight"), 2000); } });
      $("answer").append(button);
    } else if (part.startsWith("**") && part.endsWith("**")) { const strong = document.createElement("strong"); strong.textContent = part.slice(2, -2); $("answer").append(strong); }
    else if (part.startsWith("`") && part.endsWith("`")) { const code = document.createElement("code"); code.textContent = part.slice(1, -1); $("answer").append(code); }
    else $("answer").append(document.createTextNode(part));
  }
  $("answer-empty").hidden = Boolean(text); $("answer").hidden = !text;
}
function renderMetrics(metrics, elapsed) {
  const labels = [];
  if (metrics.generation_tok_s != null) labels.push(`${fmt(metrics.generation_tok_s)} tokens/s`);
  if (metrics.output_tokens != null) labels.push(`${fmt(metrics.output_tokens)} tokens`);
  if (metrics.first_content_seconds != null) labels.push(`Primera respuesta: ${fmt(metrics.first_content_seconds)} s`);
  if (metrics.retrieval_seconds != null) labels.push(`Búsqueda: ${fmt(metrics.retrieval_seconds)} s`);
  if (elapsed != null) labels.push(`Total: ${fmt(elapsed)} s`);
  if (metrics.done_reason === "length") labels.push("Límite de salida alcanzado");
  $("answer-metrics").replaceChildren();
  for (const label of labels) { const element = document.createElement("span"); element.className = "metric-tag"; element.textContent = label; $("answer-metrics").append(element); }
  $("answer-metrics").hidden = !labels.length;
}
function showJob(job, elapsed) {
  $("job-phase").textContent = job.phase;
  $("job-detail").textContent = job.error || job.message || `Tiempo transcurrido: ${fmt(job.elapsed_seconds ?? elapsed)} s`;
  $("job-dot").classList.toggle("active-dot", ["running", "queued"].includes(job.status));
  if (job.kind === "query") {
    renderSources(job.sources || []); renderAnswer(job.answer || "");
    renderMetrics(job.metrics || {}, job.elapsed_seconds);
    const running = ["running", "queued"].includes(job.status);
    $("answer-status").textContent = running ? `${job.phase} · ${fmt(elapsed)} s` : (job.status === "error" ? "No completada" : "Consulta completada");
    $("answer-status").classList.toggle("running", running);
  }
}
async function followJob(jobId) {
  if (state.busy) return;
  state.busy = true; state.activeJob = jobId; syncButtons();
  const started = performance.now();
  try {
    while (true) {
      const job = await api(`/api/jobs/${jobId}`);
      showJob(job, (performance.now() - started) / 1000);
      if (["done", "error"].includes(job.status)) {
        if (job.status === "error") notice(job.error);
        else if (job.kind !== "query") notice(job.message || "Tarea completada.", true);
        if (job.kind === "query" && job.status === "done") sessionStorage.setItem("rag-local-result", JSON.stringify(job));
        break;
      }
      await delay(500);
    }
  } catch (error) { notice(error.message); }
  finally { state.busy = false; state.activeJob = null; syncButtons(); const status = await api("/api/status").catch(() => null); if (status) showStatus(status); }
}
async function postJob(path, payload) {
  if (state.busy) return;
  notice("");
  try {
    const result = await api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload || {})});
    await followJob(result.job_id);
  } catch (error) { notice(error.message); syncButtons(); }
}
function selectFiles(files) {
  if (state.busy) return;
  const list = [...files];
  if (list.length > 20 || list.some((file) => file.size > 20 * 1024 * 1024) || list.reduce((sum, file) => sum + file.size, 0) > 100 * 1024 * 1024) { notice("Selecciona hasta 20 archivos, de 20 MB cada uno y 100 MB en total."); return; }
  if (list.some((file) => !/\.(pdf|txt|md|markdown)$/i.test(file.name))) { notice("Puedes añadir PDF, TXT y Markdown."); return; }
  state.files = list; $("selected-files").replaceChildren();
  for (const file of list) { const row = document.createElement("li"); const name = document.createElement("span"); name.textContent = file.name; const size = document.createElement("small"); size.textContent = bytes(file.size); row.append(name, size); $("selected-files").append(row); }
  syncButtons();
}

$("query-view").hidden = admin; $("admin-view").hidden = !admin;
$(admin ? "nav-admin" : "nav-query").setAttribute("aria-current", "page");
$("breadcrumb-current").textContent = admin ? "Administración" : "Consulta";
document.title = `${admin ? "Administración" : "Consulta"} · RAG Local`;
$("server-address").textContent = location.host;
$("refresh-button").addEventListener("click", refreshStatus);
$("question").addEventListener("input", syncButtons);
$("search-only").addEventListener("change", syncButtons);
$("model").addEventListener("change", () => { localStorage.setItem("rag-local-model", $("model").value); syncButtons(); });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { $("question").value = button.dataset.prompt; $("question").focus(); syncButtons(); }));
$("question").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !$("ask-button").disabled) { event.preventDefault(); $("query-form").requestSubmit(); } });
$("query-form").addEventListener("submit", async (event) => { event.preventDefault(); sessionStorage.setItem("rag-local-question", $("question").value); await postJob("/api/query", {question: $("question").value, model: $("model").value || null, k: Number($("top-k").value), search_only: $("search-only").checked}); });
$("file-input").addEventListener("change", (event) => selectFiles(event.target.files));
$("auto-index").addEventListener("change", () => { $("upload-button").firstChild.textContent = $("auto-index").checked ? "Subir e indexar" : "Subir documentos"; });
for (const name of ["dragenter", "dragover"]) $("drop-zone").addEventListener(name, (event) => { event.preventDefault(); $("drop-zone").classList.add("dragging"); });
for (const name of ["dragleave", "drop"]) $("drop-zone").addEventListener(name, (event) => { event.preventDefault(); $("drop-zone").classList.remove("dragging"); if (name === "drop") selectFiles(event.dataTransfer.files); });
$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault(); if (state.busy || !state.files.length) return; notice("");
  const body = new FormData(); state.files.forEach((file) => body.append("files", file)); body.append("auto_index", String($("auto-index").checked));
  $("upload-button").disabled = true;
  try { const result = await api("/api/admin/upload", {method: "POST", body}); selectFiles([]); $("file-input").value = ""; if (result.job_id) await followJob(result.job_id); else { notice(`${result.saved.length} documento(s) añadido(s).`, true); await refreshStatus(); } }
  catch (error) { notice(error.message); } finally { syncButtons(); }
});
$("index-button").addEventListener("click", () => postJob("/api/admin/index"));
$("prepare-button").addEventListener("click", () => postJob("/api/admin/prepare"));
async function init() {
  try {
    state.token = (await api("/api/session")).token;
    $("question").value = sessionStorage.getItem("rag-local-question") || "";
    const previous = sessionStorage.getItem("rag-local-result"); if (previous) showJob(JSON.parse(previous), 0);
    await refreshStatus(); setInterval(() => { if (!state.busy) refreshStatus(); }, 12000);
  } catch (error) { notice(`No se puede conectar con el servidor local: ${error.message}`); }
}
init();
