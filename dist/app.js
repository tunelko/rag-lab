import {$, node, icon, iconButton, button, fileType, progress, segments, number, duration, normalize, renderText, renderSources, showResult, errorData, toast, announce, copyText} from './ui.js';
import {UploadQueue} from './queue.js';
import {Chat} from './chat.js';
import {ServerConsole} from './console.js';
import {Telemetry} from './telemetry.js';

const state={token:'',status:null,k:4,filter:'all',search:'',page:1,view:'query',queryJob:null,querySubmitting:false,queryCancel:false,lastQuery:null,answer:'',sources:[],sourceKey:'',newDocuments:new Set(JSON.parse(sessionStorage.getItem('rag-local-new-documents')||'[]')),lastResult:null,operationJobs:new Set(),lastPhase:null};
let queue, chat, serverConsole, telemetry;
$('server-address').textContent=location.host;
let preferences = null; let modelList = [];
state.lastIndex=Number(sessionStorage.getItem('rag-local-last-index')||0);
async function api(path,options={}){
  const headers={...(options.headers||{})};if(options.method&&options.method!=='GET')headers['X-RAG-Token']=state.token;
  let response,result;
  try{response=await fetch(path,{...options,headers});result=await response.json();}
  catch(error){const problem=new Error('Se perdió la conexión con el servidor local.');problem.info=errorData({code:'NETWORK',detail:error.message});throw problem;}
  if(!response.ok){const message=typeof result.detail==='string'?result.detail:'No se ha podido completar la operación.';const problem=new Error(message);problem.status=response.status;problem.info=errorData(result.error_info||{message,detail:JSON.stringify(result.detail)});throw problem;}
  return result;
}
function navigate(path,replace=false){
  const views={'/':['query','query-view','nav-query','Chat'],'/admin':['library','admin-view','nav-admin','Biblioteca'],'/biblioteca':['library','admin-view','nav-admin','Biblioteca'],'/configuracion':['settings','settings-view','nav-settings','Configuración'],'/estadisticas':['statistics','statistics-view','nav-statistics','Estadísticas'],'/consola':['console','console-view','nav-console','Consola']};
  const route=new URL(path,location.origin);const target=views[route.searchParams.get('vista')==='consola'?'/consola':route.pathname]||views['/'];state.view=target[0];
  if(state.view!=='statistics'&&$('resource-dialog').open)$('resource-dialog').close();
  if(location.pathname+location.search!==path)history[replace?'replaceState':'pushState']({},'',path);
  for(const id of ['query-view','admin-view','settings-view','statistics-view','console-view'])$(id).hidden=id!==target[1];
  document.querySelectorAll('.nav-item').forEach(link=>link.removeAttribute('aria-current'));$(target[2]).setAttribute('aria-current','page');
  $('breadcrumb-current').textContent=target[3];document.title=target[3]+' · RAG Local';
  if(state.view==='library'&&state.lastResult)$('queue-result-button').hidden=false;
  if(state.view==='statistics')refreshStatistics();
  serverConsole?.setActive(state.view==='console');
  telemetry?.setActive(state.view==='statistics');
}
document.addEventListener('click',(event)=>{const link=event.target.closest('a[href]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;const url=new URL(link.href,location.href);if(url.origin===location.origin&&['/','/admin','/biblioteca','/configuracion','/estadisticas','/consola'].includes(url.pathname)&&!url.hash){event.preventDefault();navigate(url.pathname+url.search);window.scrollTo({top:0,behavior:'instant'});}});
window.addEventListener('popstate',()=>navigate(location.pathname+location.search,true));
window.addEventListener('rag:navigate',(event)=>navigate(event.detail));


function statusView(status){
  // Older servers mark every member of a running batch as processing.
  // Completed outcomes and the current file provide the actual per-file state.
  status.documents=status.documents.map(document=>{
    const job=(status.jobs||[]).find(job=>job.kind==='index'&&!job.document_name&&job.document_names?.includes(document.name));
    if(!job)return document;
    const outcome=job.documents?.find(item=>item.name===document.name||item.name===document.name.split('/').at(-1));
    if(outcome?.error_info)return {...document,status:'error',error_info:outcome.error_info};
    if(outcome?.chunks>0)return {...document,status:'indexed',indexed:true,chunks:outcome.chunks};
    if(job.current_document===document.name||job.current_document===document.name.split('/').at(-1))return {...document,status:'processing'};
    return {...document,status:document.indexed?'indexed':'pending'};
  });
  state.status=status;queue?.setDocuments(status.documents);
  $('stat-docs').textContent=number(status.document_count);$('stat-chunks').textContent=number(status.chunk_count);$('sidebar-doc-count').textContent=number(status.document_count);
  $('library-count').textContent=number(status.document_count);$('stat-engine').textContent=status.ollama.connected?'Ollama conectado':'Ollama sin conexión';
  $('stat-engine-caption').textContent='El modelo se carga al consultar';$('ollama-label').textContent=status.ollama.connected?'Conectado':'Sin conexión';
  const busy=(status.jobs||[]).some((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));$('embedding-label').textContent=`${status.embedding_device.toUpperCase()}${busy?' · ocupado':''}`;
  $('empty-banner').hidden=status.chunk_count>0;$('config-collection').textContent=status.collection;$('footer-collection').textContent=`Colección: ${status.collection}`;
  $('config-embeddings').textContent=status.embedding_model;$('config-device').textContent=status.embedding_device.toUpperCase();$('config-chunk').textContent=`${status.chunk_size} / ${status.chunk_overlap} car.`;
  const indexed=status.documents.filter((doc)=>doc.status==='indexed'||(!doc.status&&doc.indexed)).length;const failed=status.documents.filter((doc)=>doc.status==='error').length;const processing=status.documents.filter(doc=>doc.status==='processing').length;const pending=status.document_count-indexed-failed-processing;
  segments($('library-bar'),[100*indexed/(status.document_count||1),100*(pending+processing)/(status.document_count||1),100*failed/(status.document_count||1)]);
  $('library-bar').setAttribute('aria-label',`${indexed} indexados, ${processing} en proceso, ${pending} pendientes y ${failed} con error`);
  $('sidebar-summary-text').textContent=`${indexed} indexados · ${processing} en proceso · ${pending} pendientes · ${failed} con error`;
  if(state.lastIndex)$('last-index').textContent=`Última indexación ${relativeTime(new Date(state.lastIndex).toISOString())}`;
  else if(status.chunk_count)$('last-index').textContent='Fragmentos guardados en disco';
  if(!queue?.entries.some((entry)=>['queued','uploading','uploaded','fragmenting','indexing'].includes(entry.state))){$('nav-badge').textContent=status.document_count;$('nav-badge').classList.remove('queued');}
  $('index-button').disabled=!status.document_count;
  renderDocuments();renderProcess();chat?.setStatus(status);
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
  $('table-caption').textContent=`Mostrando ${visible.length} de ${filtered.length} · Los archivos sin cambios se omiten al actualizar. Las imágenes y los PDF escaneados requieren el modo multimodal.`;
}
function renderProcess(){
  const status=state.status;const job=(status?.jobs||[]).find((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));
  const uploading=queue?.entries.find((entry)=>entry.state==='uploading');
  if(job)state.lastPhase={stage:job.stage,value:job.stage_progress??null,name:job.current_document||job.document_name||'',active:true};
  else if(uploading)state.lastPhase={stage:'upload',value:uploading.upload,name:uploading.name,active:true};
  else if(state.lastPhase)state.lastPhase={...state.lastPhase,active:false};
  const current=state.lastPhase;const stages=['upload','chunking','indexing'];const index=current?stages.indexOf(current.stage):-1;
  $('process-state').textContent=current?.active?(job?.document_names?.length>1?`${job.documents?.length||0}/${job.document_names.length} documentos · activo`:'Activo'):'Inactivo';$('process-state').className=`status-pill ${current?.active?'processing':''}`;
  document.querySelectorAll('#process-steps li').forEach((step,i)=>{const complete=current&&(i<index||!current.active);const active=current?.active&&i===index;step.classList.toggle('active',Boolean(active));step.classList.toggle('complete',Boolean(complete));step.querySelector('.step-number').textContent=complete?'✓':i+1;const bar=step.querySelector('progress');if(active&&current.value==null)bar.removeAttribute('value');else bar.value=active?(current.value||0):complete?100:0;bar.className='ochre';});
  $('process-document').textContent=current?.name||'Fragmentación del contenido';
  const activities=status?.activity||[];$('activity-log').replaceChildren();if(!activities.length)$('activity-log').append(node('p','','Sin actividad reciente.'));
  for(const activity of activities.slice(-6)){const line=node('p');const time=node('time','',new Date(activity.time).toLocaleTimeString('es-ES'));time.dateTime=activity.time;line.append(time,document.createTextNode(`${activity.document?activity.document+' · ':''}${activity.text}`));$('activity-log').append(line);}
}
function completed(result,opener=$('upload-button')){
  state.lastResult=result;$('queue-result-button').hidden=false;
  for(const item of result.successes||[])if(item.chunks>0&&!item.skipped)state.newDocuments.add(item.name);
  sessionStorage.setItem('rag-local-new-documents',JSON.stringify([...state.newDocuments]));
  if(result.kind!=='prepare'&&!result.unindexed&&(result.successes||[]).some((item)=>item.chunks>0&&!item.skipped)){state.lastIndex=Date.now();sessionStorage.setItem('rag-local-last-index',String(state.lastIndex));$('last-index').textContent='Última indexación ahora';}
  if(state.view==='library')showResult(result,opener);else toast(result.kind==='prepare'?'Modelo de búsqueda listo · Ver resultado':'Indexación terminada · Ver resultado',()=>showResult(result,opener));
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
        const records=job.documents||[];const successes=records.filter((item)=>!item.error_info).map((item)=>({name:item.name,chunks:item.chunks||0,skipped:item.skipped}));
        const failures=records.filter((item)=>item.error_info).map((item)=>({name:item.name,error:item.error_info,retry:()=>startOperation('/api/admin/index','index',{file:item.name})}));
        if(job.status==='error'&&!failures.length)failures.push({name:kind==='prepare'?'Modelo de búsqueda':payload.file||'Actualización del índice',error:job.error_info||{message:job.error},retry:()=>startOperation(path,kind,payload)});
        completed({kind,successes,failures,summary:job.summary,duration:job.elapsed_seconds||0},opener);break;
      }
      await new Promise((resolve)=>setTimeout(resolve,1000));
    }
  }catch(error){completed({kind,successes:[],failures:[{name:kind==='prepare'?'Modelo de búsqueda':'Actualización del índice',error:error.info||{code:'NETWORK',detail:error.message},retry:()=>startOperation(path,kind,payload)}]},opener);}
  finally{opener.disabled=false;refreshStatus();}
}
$('refresh-button').addEventListener('click',refreshStatus);$('prepare-button').addEventListener('click',()=>startOperation('/api/admin/prepare','prepare'));$('index-button').addEventListener('click',()=>startOperation('/api/admin/index','index'));
$('queue-result-button').addEventListener('click',()=>state.lastResult&&showResult(state.lastResult,$('queue-result-button')));
let searchTimer;$('document-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.search=$('document-search').value;state.page=1;renderDocuments();},150);});
$('document-filters').addEventListener('click',(event)=>{const control=event.target.closest('[data-filter]');if(control){state.filter=control.dataset.filter;state.page=1;renderDocuments();}});
$('previous-page').addEventListener('click',()=>{state.page--;renderDocuments();});$('next-page').addEventListener('click',()=>{state.page++;renderDocuments();});
function responseMode(){
  const automatic=$('model-selection').value==='automatic';
  const visual=modelList.find(item=>item.name===$('model').value)?.vision;
  $('response-model-label').textContent=automatic?'Modelo para consultas de texto':'Modelo de respuesta fijo';
  $('response-mode').textContent=automatic?'El chat usa este modelo para texto. Si la pregunta necesita imágenes o la evidencia principal es visual, elige el modelo visual configurado abajo. La decisión y el motivo aparecen en Estadísticas.':visual?'El modelo fijo admite imágenes originales al responder.':'El modelo fijo responde con texto y descripciones visuales; no inspecciona las imágenes originales.';
}
function fillSelect(id,items,value){
  const select=$(id);select.replaceChildren();
  const names=items.map(item=>item.name);
  if(value&&!names.includes(value))items=[{name:value,missing:true},...items];
  for(const item of items){const option=node('option','',item.name+(item.missing?' · no disponible':item.vision?' · visión':''));option.value=item.name;select.append(option);}
  if(!items.length){const option=node('option','','No hay modelos disponibles');option.value='';select.append(option);}
  select.value=value||items[0]?.name||'';
}
async function loadSettings(){
  const result=await api('/api/settings');preferences=result.settings;modelList=result.models;
  $('model-selection').value=preferences.response_mode;
  fillSelect('model',modelList,preferences.response_model);fillSelect('vision-model',modelList.filter(item=>item.vision),preferences.vision_model);
  state.k=preferences.k;$('search-only').checked=preferences.search_only;$('multimodal').checked=preferences.multimodal;
  $('max-images').value=preferences.max_images;$('max-visuals').value=preferences.max_visuals_per_document;
  $('top-k').querySelectorAll('button').forEach(control=>control.setAttribute('aria-pressed',String(Number(control.dataset.k)===state.k)));responseMode();
}
$('model').addEventListener('change',responseMode);
$('model-selection').addEventListener('change',responseMode);
$('top-k').addEventListener('click',event=>{const control=event.target.closest('[data-k]');if(!control)return;state.k=Number(control.dataset.k);$('top-k').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button===control)));});
$('settings-form').addEventListener('input',()=>{$('settings-message').textContent='Cambios sin guardar';});
$('top-k').addEventListener('click',()=>{$('settings-message').textContent='Cambios sin guardar';});
$('settings-form').addEventListener('submit',async event=>{
  event.preventDefault();$('save-settings').disabled=true;$('settings-message').textContent='Guardando…';
  try{
    const settings={response_mode:$('model-selection').value,response_model:$('model').value,k:state.k,search_only:$('search-only').checked,multimodal:$('multimodal').checked,vision_model:$('vision-model').value,max_images:Number($('max-images').value),max_visuals_per_document:Number($('max-visuals').value)};
    const result=await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)});
    preferences=result.settings;$('settings-message').textContent='Configuración guardada';toast('Configuración guardada');
  }catch(error){$('settings-message').textContent=error.message;}finally{$('save-settings').disabled=false;}
});
async function refreshStatistics(){
  try{
    const stats=await api('/api/statistics');$('statistics-scope').textContent=stats.scope;
    $('statistics-totals').textContent=stats.queries+' consultas · '+stats.completed+' completadas · '+stats.errors+' errores · '+number(stats.output_tokens)+' tokens de salida';
    $('visual-count').textContent=number(stats.visuals)+' vistas o figuras conservadas en el índice.';
    $('query-statistics').replaceChildren();
    for(const item of stats.recent){
      const metrics=item.metrics||{};const row=node('tr');
      for(const value of [item.question,metrics.model||'—',item.elapsed_seconds==null?'—':number(item.elapsed_seconds,1)+' s',metrics.generation_tok_s==null?'—':number(metrics.generation_tok_s,2),metrics.output_tokens??'—',metrics.visual_mode==='images'?metrics.images_used+' imágenes':metrics.visual_mode==='descriptions'?'Descripciones':'Texto',{done:'Completada',error:'Error',running:'En curso',queued:'En cola',cancelled:'Detenida'}[item.status]||item.status])row.append(node('td','',value));
      const timeCell=row.children[2];if(item.queue_seconds>=1)timeCell.append(node('p','routing-reason','Espera en cola: '+duration(item.queue_seconds)));
      if(metrics.vector_search_seconds!=null)timeCell.title=`Embeddings: ${number(metrics.embedding_setup_seconds||0,3)} s · Búsqueda: ${number(metrics.vector_search_seconds,3)} s · Fuentes: ${number(metrics.source_preparation_seconds||0,3)} s · Selección/preparación: ${number(metrics.routing_seconds||0,3)} s · Generación: ${number(metrics.generation_wall_seconds||0,3)} s`;
      const modelCell=row.children[1];if(metrics.routing_reason){modelCell.append(node('p','routing-reason',`${{automatic:'Automática',search_only:'Solo búsqueda',catalog:'Registro del índice'}[metrics.routing_mode]||'Manual'} · ${metrics.routing_reason}`));}
      if(metrics.retrieval_mode==='global')modelCell.append(node('p','routing-reason',`Biblioteca completa: ${metrics.documents_retrieved}/${metrics.documents_indexed} documentos`));
      $('query-statistics').append(row);
    }
    if(!stats.recent.length){const row=node('tr');const cell=node('td','','Todavía no hay consultas en este arranque del servidor.');cell.colSpan=7;row.append(cell);$('query-statistics').append(row);}
  }catch(error){toast(error.message);}
}
$('refresh-statistics').addEventListener('click',refreshStatistics);
navigate(location.pathname+location.search,true);
async function init(){
  try{
    state.token=(await api('/api/session')).token;
    serverConsole=new ServerConsole(api);serverConsole.setActive(state.view==='console');
    telemetry=new Telemetry(api);telemetry.setActive(state.view==='statistics');
    queue=new UploadQueue({api,token:()=>state.token,completed:(result)=>completed(result),changed:()=>renderProcess()});
    chat=new Chat(api,()=>{if(state.view==='statistics')refreshStatistics();});
    await loadSettings();
    await refreshStatus();chat.restore(state.status?.jobs||[]);
    setInterval(()=>{if(!document.hidden){refreshStatus();if(state.view==='statistics')refreshStatistics();}},15000);
  }catch(error){toast(error.message);}
}
init();

