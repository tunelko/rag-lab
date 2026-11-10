import {t, intlLocale, serverText, initI18n} from './i18n.js';
import {$, node, icon, iconButton, button, fileType, progress, segments, number, duration, normalize, renderText, renderSources, showResult, errorData, toast, announce, copyText} from './ui.js';
import {UploadQueue} from './queue.js';
import {Chat} from './chat.js';
import {ServerConsole} from './console.js';
import {Telemetry} from './telemetry.js';

await initI18n();

const state={token:'',status:null,k:4,filter:'all',search:'',page:1,view:'query',queryJob:null,querySubmitting:false,queryCancel:false,lastQuery:null,answer:'',sources:[],sourceKey:'',newDocuments:new Set(JSON.parse(sessionStorage.getItem('rag-local-new-documents')||'[]')),lastResult:null,operationJobs:new Set(),lastPhase:null};
let queue, chat, serverConsole, telemetry;
$('server-address').textContent=location.host;
let preferences = null; let modelList = [];
state.lastIndex=Number(sessionStorage.getItem('rag-local-last-index')||0);
async function api(path,options={},refreshed=false){
  const headers={...(options.headers||{})};if(options.method&&options.method!=='GET')headers['X-RAG-Token']=state.token;
  let response,result;
  try{response=await fetch(path,{...options,headers});result=await response.json();}
  catch(error){const problem=new Error(t("ui.the_connection_to_the_local_server_was_lost_8caf59"));problem.info=errorData({code:'NETWORK',detail:error.message});throw problem;}
  if(response.status===403&&!refreshed&&options.method&&options.method!=='GET'&&typeof result.detail==='string'&&result.detail.includes('Recarga esta página')){
    state.token=(await api('/api/session')).token;return api(path,options,true);
  }
  if(!response.ok){const message=typeof result.detail==='string'?serverText(result.detail):t("ui.the_operation_could_not_be_completed_4d19f8");const problem=new Error(message);problem.status=response.status;problem.info=errorData(result.error_info||{message,detail:JSON.stringify(result.detail)});throw problem;}
  if(path==='/api/session'&&result.token)state.token=result.token;
  return result;
}
function navigate(path,replace=false){
  const views={'/':['query','query-view','nav-query','Chat'],'/admin':['library','admin-view','nav-admin',t("ui.library_c9169c")],'/biblioteca':['library','admin-view','nav-admin',t("ui.library_c9169c")],'/configuracion':['settings','settings-view','nav-settings',t("ui.settings_bb5071")],'/estadisticas':['statistics','statistics-view','nav-statistics',t("ui.statistics_bb847b")],'/consola':['console','console-view','nav-console',t("ui.console_e0236e")]};
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
  $('library-count').textContent=number(status.document_count);$('stat-engine').textContent=status.ollama.connected?t("ui.ollama_connected_495ce4"):t("ui.ollama_offline_e80961");
  $('stat-engine-caption').textContent=t("ui.the_model_loads_when_a_query_is_made_da4ba3");$('ollama-label').textContent=status.ollama.connected?t("ui.connected_7444e3"):t("ui.offline_39b889");
  const busy=(status.jobs||[]).some((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));$('embedding-label').textContent=`${status.embedding_device.toUpperCase()}${busy?t("ui.busy_b0c299"):''}`;
  $('empty-banner').hidden=status.chunk_count>0;$('config-collection').textContent=status.collection;$('footer-collection').textContent=t("ui.collection_fce24f", {value1: (status.collection)});
  $('config-embeddings').textContent=status.embedding_model;$('config-device').textContent=status.embedding_device.toUpperCase();$('config-chunk').textContent=t("ui.chars_8d211b", {value1: (status.chunk_size), value2: (status.chunk_overlap)});
  const indexed=status.documents.filter((doc)=>doc.status==='indexed'||(!doc.status&&doc.indexed)).length;const failed=status.documents.filter((doc)=>doc.status==='error').length;const processing=status.documents.filter(doc=>doc.status==='processing').length;const pending=status.document_count-indexed-failed-processing;
  segments($('library-bar'),[100*indexed/(status.document_count||1),100*(pending+processing)/(status.document_count||1),100*failed/(status.document_count||1)]);
  $('library-bar').setAttribute('aria-label',t("ui.indexed_processing_pending_and_with_errors_c09599", {value1: (indexed), value2: (processing), value3: (pending), value4: (failed)}));
  $('sidebar-summary-text').textContent=t("ui.indexed_processing_pending_with_errors_b41e14", {value1: (indexed), value2: (processing), value3: (pending), value4: (failed)});
  if(state.lastIndex)$('last-index').textContent=t("ui.last_indexing_0e4177", {value1: (relativeTime(new Date(state.lastIndex).toISOString()))});
  else if(status.chunk_count)$('last-index').textContent=t("ui.chunks_saved_on_disk_ac49e3");
  if(!queue?.entries.some((entry)=>['queued','uploading','uploaded','fragmenting','indexing'].includes(entry.state))){$('nav-badge').textContent=status.document_count;$('nav-badge').classList.remove('queued');}
  $('index-button').disabled=!status.document_count;
  renderDocuments();renderProcess();chat?.setStatus(status);
}
async function refreshStatus(){try{statusView(await api('/api/status'));}catch(error){toast(error.message);}}
function relativeTime(value){
  if(!value)return '—';const seconds=Math.max(0,(Date.now()-Date.parse(value))/1000);
  if(seconds<60)return t("ui.now_078f91");if(seconds<3600)return t("ui.min_ago_7ddff1", {value1: (Math.floor(seconds/60))});if(seconds<86400)return t("ui.h_ago_27eb8e", {value1: (Math.floor(seconds/3600))});return t("ui.days_ago_50545b", {value1: (Math.floor(seconds/86400))});
}
function renderDocuments(){
  const docs=state.status?.documents||[];const matching=docs.filter((doc)=>normalize(doc.name).includes(normalize(state.search)));
  const normalizedState=(doc)=>doc.status||(doc.indexed?'indexed':'pending');
  const counts={all:matching.length,indexed:matching.filter((doc)=>normalizedState(doc)==='indexed').length,processing:matching.filter((doc)=>normalizedState(doc)==='processing').length,error:matching.filter((doc)=>normalizedState(doc)==='error').length};
  const filterNames={all:t("ui.all_bd02b9"),indexed:t("ui.indexed_e20051"),processing:t("ui.processing_63cceb"),error:t("ui.with_errors_93a462")};
  document.querySelectorAll('[data-filter]').forEach((control)=>{control.textContent=`${filterNames[control.dataset.filter]} · ${counts[control.dataset.filter]}`;control.setAttribute('aria-pressed',String(state.filter===control.dataset.filter));});
  const filtered=matching.filter((doc)=>state.filter==='all'||normalizedState(doc)===state.filter);const pages=Math.max(1,Math.ceil(filtered.length/20));state.page=Math.min(state.page,pages);const visible=filtered.slice((state.page-1)*20,state.page*20);
  $('documents-table').replaceChildren();
  for(const doc of visible){
    const row=node('tr',`doc-state-${normalizedState(doc)}`);const name=node('td');const nameWrap=node('div','document-name');const link=node('a','',doc.name);link.href=`/api/source?file=${encodeURIComponent(doc.name)}`;link.title=t("ui.open_in_document_73e4f6");nameWrap.append(fileType(doc.name),link);if(state.newDocuments.has(doc.name))nameWrap.append(node('span','new-badge',t("ui.new_069d9f")));name.append(nameWrap);
    const bytes=node('td','',doc.bytes<1024*1024?`${number(doc.bytes/1024,1)} KB`:`${number(doc.bytes/1024/1024,1)} MB`);
    const status=node('td');const label=node('div',`doc-status${normalizedState(doc)==='processing'?' progressing':''}`);const dot=node('i',`status-dot ${normalizedState(doc)==='error'?'brick':normalizedState(doc)==='processing'?'ochre':'moss'}`);
    let text=doc.indexed?t("ui.indexed_bb7f0d"):t("ui.pending_2ef685");
    if(normalizedState(doc)==='processing')text=doc.stage==='indexing'?t("ui.indexing_8df561"):doc.stage==='chunking'?t("ui.splitting_into_chunks_726c36"):t("ui.queued_d19761");
    if(normalizedState(doc)==='error')text=doc.error_info?.code==='NO_TEXT'?t("ui.needs_ocr_6dd492"):serverText(doc.error_info?.message)||t("ui.with_errors_93a462");
    label.append(dot,node('span','',text));if(normalizedState(doc)==='processing'&&Number.isFinite(doc.progress)){label.append(progress(doc.progress,'ochre',t("ui.progress_of_d19948", {value1: (doc.name)})),node('span','mono',`${number(doc.progress)} %`));}status.append(label);
    const chunks=node('td','mono',doc.indexed?number(doc.chunks):normalizedState(doc)==='error'?'0':'—');const updated=node('td','updated-time',relativeTime(doc.updated_at));updated.title=doc.updated_at?new Date(doc.updated_at).toLocaleString(intlLocale()):'';
    const actions=node('td');if(normalizedState(doc)==='error')actions.append(button(t("ui.retry_a9254c"),'button error-outline',()=>startOperation('/api/admin/index','index',{file:doc.name})));
    row.append(name,bytes,status,chunks,updated,actions);$('documents-table').append(row);
  }
  $('library-empty').hidden=visible.length>0;$('library-empty').textContent=docs.length?t("ui.no_documents_match_this_filter_e03855"):t("ui.you_have_not_added_any_documents_yet_start_with_the_area_above_5c043e");
  $('page-label').textContent=`${state.page} / ${pages}`;$('previous-page').disabled=state.page<=1;$('next-page').disabled=state.page>=pages;
  $('table-caption').textContent=t("ui.showing_of_unchanged_files_are_skipped_when_updating_images_and_s_af8c16", {value1: (visible.length), value2: (filtered.length)});
}
function renderProcess(){
  const status=state.status;const job=(status?.jobs||[]).find((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));
  const uploading=queue?.entries.find((entry)=>entry.state==='uploading');
  if(job)state.lastPhase={stage:job.stage,value:job.stage_progress??null,name:job.current_document||job.document_name||'',active:true};
  else if(uploading)state.lastPhase={stage:'upload',value:uploading.upload,name:uploading.name,active:true};
  else if(state.lastPhase)state.lastPhase={...state.lastPhase,active:false};
  const current=state.lastPhase;const stages=['upload','chunking','indexing'];const index=current?stages.indexOf(current.stage):-1;
  $('process-state').textContent=current?.active?(job?.document_names?.length>1?t("ui.documents_active_76b844", {value1: (job.documents?.length||0), value2: (job.document_names.length)}):t("ui.active_723858")):t("ui.idle_06423d");$('process-state').className=`status-pill ${current?.active?'processing':''}`;
  document.querySelectorAll('#process-steps li').forEach((step,i)=>{const complete=current&&(i<index||!current.active);const active=current?.active&&i===index;step.classList.toggle('active',Boolean(active));step.classList.toggle('complete',Boolean(complete));step.querySelector('.step-number').textContent=complete?'✓':i+1;const bar=step.querySelector('progress');if(active&&current.value==null)bar.removeAttribute('value');else bar.value=active?(current.value||0):complete?100:0;bar.className='ochre';});
  $('process-document').textContent=current?.name||t("ui.splitting_document_content_e3067f");
  const activities=status?.activity||[];$('activity-log').replaceChildren();if(!activities.length)$('activity-log').append(node('p','',t("ui.no_recent_activity_be9328")));
  for(const activity of activities.slice(-6)){const line=node('p');const time=node('time','',new Date(activity.time).toLocaleTimeString(intlLocale()));time.dateTime=activity.time;line.append(time,document.createTextNode(`${activity.document?activity.document+' · ':''}${serverText(activity.text)}`));$('activity-log').append(line);}
}
function completed(result,opener=$('upload-button')){
  state.lastResult=result;$('queue-result-button').hidden=false;
  for(const item of result.successes||[])if(item.chunks>0&&!item.skipped&&!item.recovered)state.newDocuments.add(item.name);
  sessionStorage.setItem('rag-local-new-documents',JSON.stringify([...state.newDocuments]));
  if(result.kind!=='prepare'&&!result.unindexed&&(result.successes||[]).some((item)=>item.chunks>0&&!item.skipped&&!item.recovered)){state.lastIndex=Date.now();sessionStorage.setItem('rag-local-last-index',String(state.lastIndex));$('last-index').textContent=t("ui.last_indexing_now_7ae6c0");}
  if(state.view==='library')showResult(result,opener);else toast(result.kind==='prepare'?t("ui.retrieval_model_ready_view_result_9b1465"):t("ui.indexing_complete_view_result_2c154a"),()=>showResult(result,opener));
  refreshStatus();
}
async function startOperation(path,kind,payload={}){
  const opener=document.activeElement;opener.disabled=true;
  try{
    const {job_id}=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});state.operationJobs.add(job_id);
    while(true){
      const job=await api(`/api/jobs/${job_id}`);if(state.status){state.status.jobs=[job];renderProcess();}
      if(['done','error','cancelled'].includes(job.status)){
        if(job.status==='cancelled'){toast(t("ui.job_cancelled_5cb662"));break;}
        const records=job.documents||[];const successes=records.filter((item)=>!item.error_info).map((item)=>({name:item.name,chunks:item.chunks||0,skipped:item.skipped}));
        const failures=records.filter((item)=>item.error_info).map((item)=>({name:item.name,error:item.error_info,retry:()=>startOperation('/api/admin/index','index',{file:item.name})}));
        if(job.status==='error'&&!failures.length)failures.push({name:kind==='prepare'?t("ui.retrieval_model_3204da"):payload.file||t("ui.index_update_22cc85"),error:job.error_info||{message:job.error},retry:()=>startOperation(path,kind,payload)});
        completed({kind,successes,failures,summary:job.summary,duration:job.elapsed_seconds||0},opener);break;
      }
      await new Promise((resolve)=>setTimeout(resolve,1000));
    }
  }catch(error){completed({kind,successes:[],failures:[{name:kind==='prepare'?t("ui.retrieval_model_3204da"):t("ui.index_update_22cc85"),error:error.info||{code:'NETWORK',detail:error.message},retry:()=>startOperation(path,kind,payload)}]},opener);}
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
  $('response-model-label').textContent=automatic?t("ui.model_for_text_queries_09f215"):t("ui.fixed_response_model_087731");
  $('response-mode').textContent=automatic?t("ui.the_chat_uses_this_model_for_text_if_the_question_needs_images_or_395325"):visual?t("ui.the_fixed_model_can_inspect_original_images_when_answering_abfe69"):t("ui.the_fixed_model_answers_using_text_and_visual_descriptions_it_doe_733998");
}
function fillSelect(id,items,value){
  const select=$(id);select.replaceChildren();
  const names=items.map(item=>item.name);
  if(value&&!names.includes(value))items=[{name:value,missing:true},...items];
  for(const item of items){const option=node('option','',item.name+(item.missing?t("ui.unavailable_53f427"):item.vision?t("ui.vision_2155d3"):''));option.value=item.name;select.append(option);}
  if(!items.length){const option=node('option','',t("ui.no_models_available_359897"));option.value='';select.append(option);}
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
$('settings-form').addEventListener('input',()=>{$('settings-message').textContent=t("ui.unsaved_changes_548353");});
$('top-k').addEventListener('click',()=>{$('settings-message').textContent=t("ui.unsaved_changes_548353");});
$('settings-form').addEventListener('submit',async event=>{
  event.preventDefault();$('save-settings').disabled=true;$('settings-message').textContent=t("ui.saving_3c8dc4");
  try{
    const settings={response_mode:$('model-selection').value,response_model:$('model').value,k:state.k,search_only:$('search-only').checked,multimodal:$('multimodal').checked,vision_model:$('vision-model').value,max_images:Number($('max-images').value),max_visuals_per_document:Number($('max-visuals').value)};
    const result=await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)});
    preferences=result.settings;$('settings-message').textContent=t("ui.settings_saved_ac6cc6");toast(t("ui.settings_saved_ac6cc6"));
  }catch(error){$('settings-message').textContent=error.message;}finally{$('save-settings').disabled=false;}
});
let statisticsWindow=604800,statisticsPage=1,ingestionPage=1;
async function refreshStatistics(){
  const requestedWindow=statisticsWindow,requestedPage=statisticsPage,requestedIngestionPage=ingestionPage;
  try{
    const stats=await api(`/api/statistics?history_seconds=${requestedWindow}&page=${requestedPage}&ingestion_page=${requestedIngestionPage}`);
    if(requestedWindow!==statisticsWindow||requestedPage!==statisticsPage||requestedIngestionPage!==ingestionPage)return;
    statisticsPage=stats.page||1;$('statistics-scope').textContent=stats.scope;
    $('statistics-page').textContent=t("ui.page_of_queries_65312d", {value1: (statisticsPage), value2: (stats.pages||1), value3: (stats.queries)});
    $('statistics-previous').disabled=statisticsPage<=1;$('statistics-next').disabled=statisticsPage>=(stats.pages||1);
    $('statistics-totals').textContent=stats.queries+t("ui.queries_268864")+stats.completed+t("ui.completed_fed359")+stats.errors+t("ui.errors_3fe67e")+number(stats.output_tokens)+t("ui.output_tokens_ef3c29");
    $('visual-count').textContent=number(stats.visuals)+t("ui.page_views_or_figures_retained_in_the_index_af868e");
    const ingestions=stats.ingestions||{total:0,page:1,pages:1,recent:[]};ingestionPage=ingestions.page;
    $('ingestion-history-info').textContent=t("ui.same_period_as_the_queries_actual_execution_durations_and_queue_w_4f2094");
    $('ingestion-page').textContent=t("ui.page_of_jobs_35be73", {value1: (ingestionPage), value2: (ingestions.pages), value3: (ingestions.total)});
    $('ingestion-previous').disabled=ingestionPage<=1;$('ingestion-next').disabled=ingestionPage>=ingestions.pages;
    $('ingestion-statistics').replaceChildren();
    for(const item of ingestions.recent){const row=node('tr');const names=item.document_names||[];const name=item.document_name||(names.length===1?names[0]:names.length?t("ui.documents_e66909", {value1: (names.length)}):t("ui.indexing_690a97"));
      for(const value of [name,item.elapsed_seconds==null?'—':duration(item.elapsed_seconds),item.queue_seconds==null?'—':duration(item.queue_seconds),number(item.chunks||0),{done:t("ui.completed_b14859"),error:t("ui.error_54a0e8"),running:t("ui.in_progress_75f1f7"),queued:t("ui.queued_d19761"),cancelled:t("ui.stopped_e5258c"),interrupted:t("ui.interrupted_ba95ed")}[item.status]||item.status])row.append(node('td','',value));
      if(names.length)row.children[0].title=names.join('\n');if(item.created_at)row.children[0].append(node('p','routing-reason',new Date(item.created_at).toLocaleString(intlLocale())));$('ingestion-statistics').append(row);}
    $('query-statistics').replaceChildren();
    for(const item of stats.recent){
      const metrics=item.metrics||{};const row=node('tr');
      for(const value of [item.question,metrics.model||'—',item.elapsed_seconds==null?'—':number(item.elapsed_seconds,1)+' s',metrics.generation_tok_s==null?'—':number(metrics.generation_tok_s,2),metrics.output_tokens??'—',metrics.visual_mode==='images'?metrics.images_used+t("ui.images_5600b9"):metrics.visual_mode==='descriptions'?t("ui.descriptions_69fa11"):t("ui.text_6df3d6"),{done:t("ui.completed_b14859"),error:t("ui.error_54a0e8"),running:t("ui.in_progress_75f1f7"),queued:t("ui.queued_d19761"),cancelled:t("ui.stopped_e5258c"),interrupted:t("ui.interrupted_ba95ed")}[item.status]||item.status])row.append(node('td','',value));
      if(item.created_at)row.children[0].append(node('p','routing-reason',new Date(item.created_at).toLocaleString(intlLocale())));
      const timeCell=row.children[2];if(item.queue_seconds>=1)timeCell.append(node('p','routing-reason',t("ui.queue_wait_01ec0d")+duration(item.queue_seconds)));
      if(metrics.vector_search_seconds!=null)timeCell.title=t("ui.embeddings_s_retrieval_s_sources_s_selection_preparation_s_genera_860e45", {value1: (number(metrics.embedding_setup_seconds||0,3)), value2: (number(metrics.vector_search_seconds,3)), value3: (number(metrics.source_preparation_seconds||0,3)), value4: (number(metrics.routing_seconds||0,3)), value5: (number(metrics.generation_wall_seconds||0,3))});
      const modelCell=row.children[1];if(metrics.routing_reason){modelCell.append(node('p','routing-reason',`${{automatic:t("ui.automatic_e51e19"),search_only:t("ui.retrieval_only_114688"),catalog:t("ui.index_catalog_38d74a")}[metrics.routing_mode]||t("ui.manual_b0b9fe")} · ${serverText(metrics.routing_reason)}`));}
      if(metrics.retrieval_mode==='global')modelCell.append(node('p','routing-reason',t("ui.entire_library_documents_30dbb6", {value1: (metrics.documents_retrieved), value2: (metrics.documents_indexed)})));
      $('query-statistics').append(row);
    }
    if(!stats.recent.length){const row=node('tr');const cell=node('td','',t("ui.no_queries_saved_in_this_period_4fab7d"));cell.colSpan=7;row.append(cell);$('query-statistics').append(row);}
  }catch(error){toast(error.message);}
}
$('refresh-statistics').addEventListener('click',refreshStatistics);
$('statistics-history-range').addEventListener('click',event=>{const control=event.target.closest('[data-history-seconds]');if(!control)return;statisticsWindow=Number(control.dataset.historySeconds);statisticsPage=1;ingestionPage=1;$('statistics-history-range').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button===control)));refreshStatistics();});
$('statistics-previous').addEventListener('click',()=>{statisticsPage=Math.max(1,statisticsPage-1);refreshStatistics();});
$('statistics-next').addEventListener('click',()=>{statisticsPage++;refreshStatistics();});
$('ingestion-previous').addEventListener('click',()=>{ingestionPage=Math.max(1,ingestionPage-1);refreshStatistics();});
$('ingestion-next').addEventListener('click',()=>{ingestionPage++;refreshStatistics();});
navigate(location.pathname+location.search,true);
window.addEventListener('rag:languagechange', () => {
  // Repaint existing state; changing language must not submit or restart work.
  navigate(location.pathname + location.search, true);
  if (state.status) statusView(state.status);
  queue?.render();
  const scroll = $('chat-thread').scrollTop;
  chat?.render(); $('chat-thread').scrollTop = scroll;
  serverConsole?.render();
  if (telemetry?.display) {
    const shown = telemetry.display;
    telemetry.render(shown.data);
    if (telemetry.paused) { telemetry.display = shown; telemetry.draw(shown); }
  }
  if (telemetry) {
    $('hardware-pause').textContent = telemetry.paused ? t('controls.resume') : t('controls.pause_charts');
    $('hardware-pause').setAttribute('aria-pressed', String(telemetry.paused));
  }
  if (serverConsole) $('console-pause').textContent = serverConsole.paused ? t('controls.resume') : t('controls.pause');
  for (const id of ['model', 'vision-model']) {
    const current = $(id).value;
    fillSelect(id, id === 'model' ? modelList : modelList.filter(item => item.vision), current);
  }
  responseMode();
  if ($('resource-dialog').open) $('resource-dialog-title').textContent = t('charts.performance', {label: document.querySelector('.resource-expanded h3')?.textContent || ''});
  for (const id of ['settings-message', 'console-status', 'hardware-status']) $(id).textContent = serverText($(id).textContent);
  if (!$('toast').hidden) $('toast').querySelector('span').textContent = serverText($('toast').querySelector('span').textContent);
  if ($('result-dialog').open && state.lastResult) showResult(state.lastResult);
});
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

