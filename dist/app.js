import {$, node, icon, iconButton, button, fileType, progress, segments, number, duration, normalize, renderText, renderSources, showResult, errorData, toast, announce, copyText} from './ui.js';
import {UploadQueue} from './queue.js';

const state={token:'',status:null,k:4,filter:'all',search:'',page:1,view:'query',queryJob:null,querySubmitting:false,queryCancel:false,lastQuery:null,answer:'',sources:[],sourceKey:'',newDocuments:new Set(JSON.parse(sessionStorage.getItem('rag-local-new-documents')||'[]')),lastResult:null,operationJobs:new Set(),lastPhase:null};
let queue;
async function api(path,options={}){
  const headers={...(options.headers||{})};if(options.method&&options.method!=='GET')headers['X-RAG-Token']=state.token;
  let response,result;
  try{response=await fetch(path,{...options,headers});result=await response.json();}
  catch(error){const problem=new Error('Se perdió la conexión con el servidor local.');problem.info=errorData({code:'NETWORK',detail:error.message});throw problem;}
  if(!response.ok){const message=typeof result.detail==='string'?result.detail:'No se ha podido completar la operación.';const problem=new Error(message);problem.info=errorData(result.error_info||{message,detail:JSON.stringify(result.detail)});throw problem;}
  return result;
}
function navigate(path,replace=false){
  state.view=['/admin','/biblioteca'].includes(path)?'library':'query';
  if(location.pathname!==path)history[replace?'replaceState':'pushState']({},'',path);
  $('query-view').hidden=state.view!=='query';$('admin-view').hidden=state.view!=='library';
  $('nav-query').removeAttribute('aria-current');$('nav-admin').removeAttribute('aria-current');$(state.view==='library'?'nav-admin':'nav-query').setAttribute('aria-current','page');
  $('breadcrumb-current').textContent=state.view==='library'?'Biblioteca':'Consulta';document.title=`${state.view==='library'?'Biblioteca':'Consulta'} · RAG Local`;
  if(state.view==='library'&&state.lastResult)$('queue-result-button').hidden=false;
}
document.addEventListener('click',(event)=>{const link=event.target.closest('a[href]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;const url=new URL(link.href,location.href);if(url.origin===location.origin&&['/','/admin','/biblioteca'].includes(url.pathname)&&!url.hash){event.preventDefault();navigate(url.pathname);window.scrollTo({top:0,behavior:'instant'});}});
window.addEventListener('popstate',()=>navigate(location.pathname,true));
window.addEventListener('rag:navigate',(event)=>navigate(event.detail));

function queryControls(){
  const busy=Boolean(state.queryJob||state.querySubmitting);const searchOnly=$('search-only').checked;
  $('ask-button').querySelector('span').textContent=busy?(state.queryCancel?'Deteniendo…':'Detener'):searchOnly?'Buscar':'Preguntar';
  $('ask-button').setAttribute('aria-busy',String(busy));
  $('ask-button').disabled=state.querySubmitting||state.queryCancel||(!busy&&(!state.status?.chunk_count||!$('question').value.trim()||(!searchOnly&&!$('model').value)));
  $('model').disabled=busy||searchOnly||!state.status?.ollama?.models?.length;
  $('question-help').textContent=!state.status?.chunk_count?'Añade documentos para poder consultar.':searchOnly?'Busca en CPU sin solicitar una respuesta al modelo.':'La generación empieza al pulsar «Preguntar».';
  $('copy-answer').disabled=!state.answer||busy||state.lastQuery?.search_only;
  $('regenerate-answer').disabled=!state.lastQuery||busy;
}
function statusView(status){
  state.status=status;queue?.setDocuments(status.documents);
  $('stat-docs').textContent=number(status.document_count);$('stat-chunks').textContent=number(status.chunk_count);$('sidebar-doc-count').textContent=number(status.document_count);
  $('library-count').textContent=number(status.document_count);$('stat-engine').textContent=status.ollama.connected?'Ollama conectado':'Ollama sin conexión';
  $('stat-engine-caption').textContent='El modelo se carga al consultar';$('ollama-label').textContent=status.ollama.connected?'Conectado':'Sin conexión';
  const busy=(status.jobs||[]).some((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));$('embedding-label').textContent=`${status.embedding_device.toUpperCase()}${busy?' · ocupado':''}`;
  $('empty-banner').hidden=status.chunk_count>0;$('config-collection').textContent=status.collection;$('footer-collection').textContent=`Colección: ${status.collection}`;
  $('config-embeddings').textContent=status.embedding_model;$('config-device').textContent=status.embedding_device.toUpperCase();$('config-chunk').textContent=`${status.chunk_size} / ${status.chunk_overlap} car.`;
  const indexed=status.documents.filter((doc)=>doc.status==='indexed'||(!doc.status&&doc.indexed)).length;const failed=status.documents.filter((doc)=>doc.status==='error').length;const pending=status.document_count-indexed-failed;
  segments($('library-bar'),[100*indexed/(status.document_count||1),100*pending/(status.document_count||1),100*failed/(status.document_count||1)]);
  $('library-bar').setAttribute('aria-label',`${indexed} indexados, ${pending} pendientes y ${failed} con error`);
  $('sidebar-summary-text').textContent=`${indexed} indexados · ${pending} pendientes · ${failed} con error`;
  if(!queue?.entries.some((entry)=>['queued','uploading','uploaded','fragmenting','indexing'].includes(entry.state))){$('nav-badge').textContent=status.document_count;$('nav-badge').classList.remove('queued');}
  const select=$('model');const models=status.ollama.models||[];const previous=select.value||localStorage.getItem('rag-local-model')||status.ollama.default_model;
  if(JSON.stringify([...select.options].map((option)=>option.value))!==JSON.stringify(models)){
    select.replaceChildren();for(const model of models){const option=node('option','',model);option.value=model;select.append(option);}
    if(!models.length){const option=node('option','','No hay modelos locales disponibles');option.value='';select.append(option);}
    select.value=models.includes(previous)?previous:status.ollama.default_model||'';
  }
  $('index-button').disabled=!status.document_count;
  renderDocuments();renderProcess();queryControls();
}
async function refreshStatus(){try{statusView(await api('/api/status'));}catch(error){toast(error.message);}}
function relativeTime(value){
  if(!value)return '—';const seconds=Math.max(0,(Date.now()-Date.parse(value))/1000);
  if(seconds<60)return 'ahora';if(seconds<3600)return `hace ${Math.floor(seconds/60)} min`;if(seconds<86400)return `hace ${Math.floor(seconds/3600)} h`;return `hace ${Math.floor(seconds/86400)} días`;
}
function renderDocuments(){
  const docs=state.status?.documents||[];const matching=docs.filter((doc)=>normalize(doc.name).includes(normalize(state.search)));
  const normalizedState=(doc)=>doc.status||(doc.indexed?'indexed':'pending');
  const counts={all:matching.length,indexed:matching.filter((doc)=>normalizedState(doc)==='indexed').length,processing:matching.filter((doc)=>normalizedState(doc)==='processing').length,error:matching.filter((doc)=>normalizedState(doc)==='error').length};
  const filterNames={all:'Todos',indexed:'Indexados',processing:'En proceso',error:'Con error'};
  document.querySelectorAll('[data-filter]').forEach((control)=>{control.textContent=`${filterNames[control.dataset.filter]} · ${counts[control.dataset.filter]}`;control.setAttribute('aria-pressed',String(state.filter===control.dataset.filter));});
  const filtered=matching.filter((doc)=>state.filter==='all'||normalizedState(doc)===state.filter);const pages=Math.max(1,Math.ceil(filtered.length/20));state.page=Math.min(state.page,pages);const visible=filtered.slice((state.page-1)*20,state.page*20);
  $('documents-table').replaceChildren();
  for(const doc of visible){
    const row=node('tr',`doc-state-${normalizedState(doc)}`);const name=node('td');const nameWrap=node('div','document-name');const link=node('a','',doc.name);link.href=`/api/source?file=${encodeURIComponent(doc.name)}`;link.title='Abrir en el documento';nameWrap.append(fileType(doc.name),link);if(state.newDocuments.has(doc.name))nameWrap.append(node('span','new-badge','Nuevo'));name.append(nameWrap);
    const bytes=node('td','',doc.bytes<1024*1024?`${number(doc.bytes/1024,1)} KB`:`${number(doc.bytes/1024/1024,1)} MB`);
    const status=node('td');const label=node('div',`doc-status${normalizedState(doc)==='processing'?' progressing':''}`);const dot=node('i',`status-dot ${normalizedState(doc)==='error'?'brick':normalizedState(doc)==='processing'?'ochre':'moss'}`);
    let text=doc.indexed?'Indexado':'Pendiente';
    if(normalizedState(doc)==='processing')text=doc.stage==='indexing'?'Indexando':doc.stage==='chunking'?'Fragmentando':'En cola';
    if(normalizedState(doc)==='error')text=doc.error_info?.code==='NO_TEXT'?'Necesita OCR':doc.error_info?.message||'Con error';
    label.append(dot,node('span','',text));if(normalizedState(doc)==='processing'&&Number.isFinite(doc.progress)){label.append(progress(doc.progress,'ochre',`Progreso de ${doc.name}`),node('span','mono',`${number(doc.progress)} %`));}status.append(label);
    const chunks=node('td','mono',doc.indexed?number(doc.chunks):normalizedState(doc)==='error'?'0':'—');const updated=node('td','updated-time',relativeTime(doc.updated_at));updated.title=doc.updated_at?new Date(doc.updated_at).toLocaleString('es-ES'):'';
    const actions=node('td');if(normalizedState(doc)==='error')actions.append(button('Reintentar','button error-outline',()=>startOperation('/api/admin/index','index',{file:doc.name})));
    row.append(name,bytes,status,chunks,updated,actions);$('documents-table').append(row);
  }
  $('library-empty').hidden=visible.length>0;$('library-empty').textContent=docs.length?'No hay documentos que coincidan con este filtro.':'Todavía no has añadido documentos. Empieza por la zona de arriba.';
  $('page-label').textContent=`${state.page} / ${pages}`;$('previous-page').disabled=state.page<=1;$('next-page').disabled=state.page>=pages;
  $('table-caption').textContent=`Mostrando ${visible.length} de ${filtered.length} · Los archivos sin cambios se omiten al actualizar. Los PDF escaneados necesitan OCR.`;
}
function renderProcess(){
  const status=state.status;const job=(status?.jobs||[]).find((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));
  const uploading=queue?.entries.find((entry)=>entry.state==='uploading');
  if(job)state.lastPhase={stage:job.stage,value:job.stage_progress??null,name:job.current_document||job.document_name||'',active:true};
  else if(uploading)state.lastPhase={stage:'upload',value:uploading.upload,name:uploading.name,active:true};
  else if(state.lastPhase)state.lastPhase={...state.lastPhase,active:false};
  const current=state.lastPhase;const stages=['upload','chunking','indexing'];const index=current?stages.indexOf(current.stage):-1;
  $('process-state').textContent=current?.active?'Activo':'Inactivo';$('process-state').className=`status-pill ${current?.active?'processing':''}`;
  document.querySelectorAll('#process-steps li').forEach((step,i)=>{const complete=current&&(i<index||!current.active);const active=current?.active&&i===index;step.classList.toggle('active',Boolean(active));step.classList.toggle('complete',Boolean(complete));step.querySelector('.step-number').textContent=complete?'✓':i+1;const bar=step.querySelector('progress');if(active&&current.value==null)bar.removeAttribute('value');else bar.value=active?(current.value||0):complete?100:0;bar.className='ochre';});
  $('process-document').textContent=current?.name||'Fragmentación del contenido';
  const activities=status?.activity||[];$('activity-log').replaceChildren();if(!activities.length)$('activity-log').append(node('p','','Sin actividad reciente.'));
  for(const activity of activities.slice(-6)){const line=node('p');const time=node('time','',new Date(activity.time).toLocaleTimeString('es-ES'));time.dateTime=activity.time;line.append(time,document.createTextNode(`${activity.document?activity.document+' · ':''}${activity.text}`));$('activity-log').append(line);}
}
function completed(result,opener=$('upload-button')){
  state.lastResult=result;$('queue-result-button').hidden=false;
  for(const item of result.successes||[])state.newDocuments.add(item.name);
  sessionStorage.setItem('rag-local-new-documents',JSON.stringify([...state.newDocuments]));
  $('last-index').textContent='Última indexación ahora';
  if(state.view==='library')showResult(result,opener);else toast('Indexación terminada · Ver resultado',()=>showResult(result,opener));
  refreshStatus();
}
async function startOperation(path,kind,payload={}){
  const opener=document.activeElement;opener.disabled=true;
  try{
    const {job_id}=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});state.operationJobs.add(job_id);
    while(true){
      const job=await api(`/api/jobs/${job_id}`);if(state.status){state.status.jobs=[job];renderProcess();}
      if(['done','error','cancelled'].includes(job.status)){
        if(job.status==='cancelled'){toast('Trabajo cancelado');break;}
        const records=job.documents||[];const successes=records.filter((item)=>!item.error_info).map((item)=>({name:item.name,chunks:item.chunks||0}));
        const failures=records.filter((item)=>item.error_info).map((item)=>({name:item.name,error:item.error_info,retry:()=>startOperation('/api/admin/index','index',{file:item.name})}));
        if(job.status==='error'&&!failures.length)failures.push({name:kind==='prepare'?'Modelo de búsqueda':payload.file||'Actualización del índice',error:job.error_info||{message:job.error},retry:()=>startOperation(path,kind,payload)});
        completed({kind,successes,failures,summary:job.summary,duration:job.elapsed_seconds||0},opener);break;
      }
      await new Promise((resolve)=>setTimeout(resolve,1000));
    }
  }catch(error){completed({kind,successes:[],failures:[{name:kind==='prepare'?'Modelo de búsqueda':'Actualización del índice',error:error.info||{code:'NETWORK',detail:error.message},retry:()=>startOperation(path,kind,payload)}]},opener);}
  finally{opener.disabled=false;refreshStatus();}
}
function showQuery(job){
  const sources=job.sources||[];state.sources=sources;const key=JSON.stringify(sources);
  if(key!==state.sourceKey){state.sourceKey=key;renderSources(sources,state.lastQuery?.question||job.question||'');}
  const active=['queued','running','processing'].includes(job.status);const raw=job.answer||'';state.answer=raw;
  const searchOnly=job.search_only??state.lastQuery?.search_only;
  const text=searchOnly&&job.status==='done'?`Mostrando los ${sources.length} fragmentos más relevantes.`:raw;
  renderText($('answer'),text,new Set(sources.map((source)=>Number(source.citation))));$('answer').hidden=!text;$('answer-empty').hidden=active||Boolean(text)||job.status==='error';
  $('answer-skeleton').hidden=!active||Boolean(raw);$('sources-skeleton').hidden=!active||sources.length>0;
  if(active&&!sources.length)$('sources-empty').hidden=true;
  $('answer-status').hidden=false;$('answer-status').textContent=active?(job.status==='queued'?'En cola':'Generando…'):job.status==='done'?'Completada':job.status==='cancelled'?'Cancelada':'Error';
  $('answer-status').className=`status-pill ${active?'processing':job.status==='error'?'error':job.status==='done'?'success':''}`;
  $('answer-meta').textContent=[job.model||job.metrics?.model||state.lastQuery?.model||'',`${sources.length} fragmentos`,job.elapsed_seconds!=null?`${number(job.elapsed_seconds,1)} s`:''].filter(Boolean).join(' · ');
  $('query-error').hidden=job.status!=='error';if(job.status==='error')$('query-error').querySelector('p').textContent=errorData(job.error_info||(typeof job.error==='object'?job.error:{message:job.error})).message;
  const metrics=job.metrics||{};const items=[];if(metrics.generation_tok_s!=null)items.push(`${number(metrics.generation_tok_s,1)} tokens/s`);if(metrics.output_tokens!=null)items.push(`${number(metrics.output_tokens)} tokens`);if(metrics.first_content_seconds!=null)items.push(`Primera respuesta: ${number(metrics.first_content_seconds,1)} s`);if(metrics.retrieval_seconds!=null)items.push(`Búsqueda: ${number(metrics.retrieval_seconds,1)} s`);
  $('answer-metrics').replaceChildren(...items.map((text)=>node('span','metric-tag',text)));$('answer-metrics').hidden=!items.length;
  const documents=new Set(sources.map((source)=>source.file)).size;$('answer-footnote').textContent=sources.length?`Basada en ${sources.length} fragmentos de ${documents} documentos · Generada en local. Revisa las fuentes antes de usarla.`:'Generada en local. Revisa las fuentes antes de usarla.';
  if(searchOnly&&job.status==='done')$('answer-footnote').textContent='Búsqueda de fragmentos completada. No se ha llamado al modelo de generación.';
  if(job.status==='cancelled')$('answer-footnote').textContent='Consulta detenida. El texto parcial puede estar incompleto.';
}
async function followQuery(jobId,restore=false){
  if(state.queryJob)return;state.queryJob=jobId;state.queryCancel=false;queryControls();
  try{while(true){const job=await api(`/api/jobs/${jobId}`);if(restore&&!state.lastQuery){state.lastQuery={question:job.question||'',model:job.model,k:job.k||4,search_only:job.search_only};$('question').value=state.lastQuery.question;$('question-echo').textContent=state.lastQuery.question;$('question-echo').hidden=false;}showQuery(job);if(['done','error','cancelled'].includes(job.status)){sessionStorage.setItem('rag-local-query-v2',JSON.stringify({query:state.lastQuery,job}));announce(job.status==='done'?'Respuesta lista':job.status==='cancelled'?'Consulta detenida':'Error al consultar','query-announcements');break;}await new Promise((resolve)=>setTimeout(resolve,1000));}}
  catch(error){showQuery({status:'error',error_info:error.info||{code:'NETWORK'},sources:state.sources,answer:state.answer});}
  finally{state.queryJob=null;state.queryCancel=false;state.querySubmitting=false;queryControls();}
}
async function runQuery(payload){
  if(state.queryJob){state.queryCancel=true;queryControls();try{await api(`/api/jobs/${state.queryJob}`,{method:'DELETE'});}catch(error){toast(error.message);state.queryCancel=false;queryControls();}return;}
  if(state.querySubmitting)return;state.querySubmitting=true;state.lastQuery={...payload};state.answer='';state.sources=[];state.sourceKey='';$('question-echo').textContent=payload.question;$('question-echo').hidden=false;$('query-error').hidden=true;$('answer-empty').hidden=true;$('answer-skeleton').hidden=false;$('sources-skeleton').hidden=false;$('sources-empty').hidden=true;renderText($('answer'),'');renderSources([],payload.question);queryControls();
  try{const result=await api('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});state.querySubmitting=false;await followQuery(result.job_id);}
  catch(error){state.querySubmitting=false;showQuery({status:'error',error_info:error.info||{code:'NETWORK'},sources:[],answer:''});queryControls();}
}
$('server-address').textContent=location.host;
$('question').addEventListener('input',queryControls);$('search-only').addEventListener('change',queryControls);
$('model').addEventListener('change',()=>{localStorage.setItem('rag-local-model',$('model').value);queryControls();});
$('top-k').addEventListener('click',(event)=>{const control=event.target.closest('[data-k]');if(!control)return;state.k=Number(control.dataset.k);$('top-k').querySelectorAll('button').forEach((button)=>button.setAttribute('aria-pressed',String(button===control)));});
document.querySelectorAll('[data-prompt]').forEach((control)=>control.addEventListener('click',()=>{$('question').value=control.dataset.prompt;$('question').focus();$('question').setSelectionRange($('question').value.length,$('question').value.length);queryControls();}));
$('query-form').addEventListener('submit',(event)=>{event.preventDefault();runQuery({question:$('question').value.trim(),model:$('model').value||null,k:state.k,search_only:$('search-only').checked});});
$('question').addEventListener('keydown',(event)=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&!$('ask-button').disabled){event.preventDefault();$('query-form').requestSubmit();}});
$('copy-answer').addEventListener('click',()=>copyText(state.answer));$('regenerate-answer').addEventListener('click',()=>state.lastQuery&&runQuery(state.lastQuery));$('retry-query').addEventListener('click',()=>state.lastQuery&&runQuery(state.lastQuery));
$('refresh-button').addEventListener('click',refreshStatus);$('prepare-button').addEventListener('click',()=>startOperation('/api/admin/prepare','prepare'));$('index-button').addEventListener('click',()=>startOperation('/api/admin/index','index'));
$('queue-result-button').addEventListener('click',()=>state.lastResult&&showResult(state.lastResult,$('queue-result-button')));
let searchTimer;$('document-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.search=$('document-search').value;state.page=1;renderDocuments();},150);});
$('document-filters').addEventListener('click',(event)=>{const control=event.target.closest('[data-filter]');if(control){state.filter=control.dataset.filter;state.page=1;renderDocuments();}});
$('previous-page').addEventListener('click',()=>{state.page--;renderDocuments();});$('next-page').addEventListener('click',()=>{state.page++;renderDocuments();});
navigate(location.pathname,true);
async function init(){
  try{
    state.token=(await api('/api/session')).token;
    queue=new UploadQueue({api,token:()=>state.token,completed:(result)=>completed(result),changed:()=>renderProcess()});
    const saved=sessionStorage.getItem('rag-local-query-v2');if(saved){try{const previous=JSON.parse(saved);state.lastQuery=previous.query;$('question').value=previous.query?.question||'';$('question-echo').textContent=previous.query?.question||'';$('question-echo').hidden=!previous.query?.question;showQuery(previous.job);}catch{sessionStorage.removeItem('rag-local-query-v2');}}
    await refreshStatus();const active=(state.status?.jobs||[]).find((job)=>job.kind==='query');if(active)followQuery(active.id,true);
    setInterval(()=>{if(!document.hidden)refreshStatus();},15000);queryControls();
  }catch(error){toast(error.message);}
}
init();

