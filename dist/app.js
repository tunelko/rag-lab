import {SettingsController} from './settings.js';
import {Shell} from './shell.js';
import {t, intlLocale, serverText, initI18n} from './i18n.js';
import {$, node, icon, iconButton, button, fileType, progress, segments, number, size, duration, normalize, renderText, renderSources, showResult, errorData, toast, announce, copyText} from './ui.js';
import {UploadQueue} from './queue.js';
import {Chat} from './chat.js';
import {ServerConsole} from './console.js';
import {Telemetry} from './telemetry.js';
import {LibraryTopics} from './topics.js';

await initI18n();

const state={token:'',status:null,k:4,filter:'all',search:'',page:1,view:'query',queryJob:null,querySubmitting:false,queryCancel:false,lastQuery:null,answer:'',sources:[],sourceKey:'',newDocuments:new Set(JSON.parse(sessionStorage.getItem('rag-local-new-documents')||'[]')),lastResult:null,operationJobs:new Set(),lastPhase:null};
let queue, chat, serverConsole, telemetry, settingsController, shell, topics;
const s=(key,values={})=>t(`sentinel.${key}`,values);
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
  if(state.view!=='query')chat?.contextPanel.close();
  if(state.view!=='statistics'&&$('resource-dialog').open)$('resource-dialog').close();
  if(location.pathname+location.search!==path)history[replace?'replaceState':'pushState']({},'',path);
  for(const id of ['query-view','admin-view','settings-view','statistics-view','console-view'])$(id).hidden=id!==target[1];
  document.querySelectorAll('.nav-item').forEach(link=>link.removeAttribute('aria-current'));$(target[2]).setAttribute('aria-current','page');
  document.title=target[3]+' · RAG Local';
  if(state.view==='library'&&state.lastResult)$('queue-result-button').hidden=false;
  state.statisticsFilter=state.view==='statistics'?route.searchParams.get('query'):null;shell?.setView(state.view);if(state.view==='statistics')refreshStatistics();
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
  state.status=status;queue?.setDocuments(status.documents);settingsController?.setStatus(status);shell?.setStatus(status);topics?.setStatus(status);
  const indexed=status.documents.filter(doc=>doc.indexed).length,processing=status.documents.filter(doc=>doc.status==='processing').length,errors=status.documents.filter(doc=>doc.status==='error').length,pending=status.document_count-indexed-processing-errors;
  for(const id of ['stat-docs','statistics-docs'])$(id).textContent=number(status.document_count);
  for(const id of ['stat-chunks','statistics-chunks'])$(id).textContent=number(status.chunk_count);
  $('library-collection').textContent=status.collection;$('library-visuals').textContent=number(status.visual_count||0);
  $('library-summary').textContent=s('library_summary',{indexed,processing,pending,errors});
  segments($('library-bar'),[100*indexed/(status.document_count||1),100*(pending+processing)/(status.document_count||1),100*errors/(status.document_count||1)]);
  $('library-bar').setAttribute('aria-label',$('library-summary').textContent);
  $('ollama-label').textContent=s(status.ollama.connected?'connected':'offline');
  const busy=(status.jobs||[]).some(job=>job.status==='running'&&['index','prepare'].includes(job.kind));
  $('embedding-label').textContent=status.embedding_device.toUpperCase()+(busy?' · '+s('busy'):'');
  $('empty-banner').hidden=status.chunk_count>0;$('config-collection').textContent=status.collection;$('config-embeddings').textContent=$('config-embeddings').title=status.embedding_model;$('config-device').textContent=status.embedding_device.toUpperCase();$('config-chunk').textContent=s('chunk_value',{size:status.chunk_size,overlap:status.chunk_overlap});
  const indexedAt=status.last_index_at||(state.lastIndex?new Date(state.lastIndex).toISOString():null);
  $('last-index').textContent=indexedAt?relativeTime(indexedAt):'—';$('last-index').title=indexedAt?new Date(indexedAt).toLocaleString(intlLocale()):'';
  $('index-button').disabled=!status.document_count;renderDocuments();renderProcess();chat?.setStatus(status);
}
let statusRequest;
function refreshStatus(){if(statusRequest)return statusRequest;statusRequest=api('/api/status').then(statusView).catch(error=>toast(error.message)).finally(()=>{statusRequest=null;});return statusRequest;}
function relativeTime(value){
  if(!value)return '—';const seconds=Math.max(0,(Date.now()-Date.parse(value))/1000);
  if(seconds<60)return t("ui.now_078f91");if(seconds<3600)return t("ui.min_ago_7ddff1", {value1: (Math.floor(seconds/60))});if(seconds<86400)return t("ui.h_ago_27eb8e", {value1: (Math.floor(seconds/3600))});return t("ui.days_ago_50545b", {value1: (Math.floor(seconds/86400))});
}
function renderDocuments(){
  const docs=state.status?.documents||[],matching=docs.filter(doc=>(!topics||topics.includes(doc))&&normalize(doc.name).includes(normalize(state.search))),status=doc=>doc.status||(doc.indexed?'indexed':'pending');
  const counts={all:matching.length,indexed:matching.filter(doc=>status(doc)==='indexed').length,processing:matching.filter(doc=>status(doc)==='processing').length,error:matching.filter(doc=>status(doc)==='error').length};
  $('library-count').textContent=number(matching.length);
  for(const control of document.querySelectorAll('[data-filter]')){control.textContent=s('filter_'+control.dataset.filter)+' · '+number(counts[control.dataset.filter]);control.setAttribute('aria-pressed',String(state.filter===control.dataset.filter));}
  const filtered=matching.filter(doc=>state.filter==='all'||status(doc)===state.filter),pages=Math.max(1,Math.ceil(filtered.length/50));state.page=Math.min(state.page,pages);const visible=filtered.slice((state.page-1)*50,state.page*50),maximum=Math.max(1,...docs.map(doc=>doc.chunks||0));
  const signature=JSON.stringify([visible,maximum,state.page,pages,filtered.length,s('documents'),[...state.newDocuments],topics?.topics.map(item=>[item.id,item.name,item.parent_id])]);
  if(state.documentRenderSignature===signature){
    for(const [index,row]of [...$('documents-table').children].entries()){const select=row.querySelector('.document-select');if(select)select.checked=topics.selected.has(visible[index].name);const updated=row.querySelector('.updated-time');if(updated)updated.textContent=relativeTime(visible[index].updated_at);}
    topics?.selection(visible.map(doc=>doc.name));return;
  }
  state.documentRenderSignature=signature;
  $('documents-table').replaceChildren();
  for(const doc of visible){
    const row=node('tr','doc-state-'+status(doc)),name=node('td'),wrap=node('div','document-name'),link=node('a','',doc.name);link.href='/api/source?file='+encodeURIComponent(doc.name);link.title=doc.name;link.target='_blank';link.rel='noopener';wrap.append(fileType(doc.name),link);if(state.newDocuments.has(doc.name))wrap.append(node('span','new-badge',s('new')));name.append(wrap);
    const bytes=node('td','num',size(doc.bytes)),chunks=node('td','chunks-cell');chunks.append(node('span','num',doc.indexed||doc.chunks?number(doc.chunks):'—'));const bar=node('span','bar'),fill=node('i');fill.style.width=100*(doc.chunks||0)/maximum+'%';bar.append(fill);chunks.append(bar);
    const stateCell=node('td'),label=node('div','doc-status'),dot=node('i','status-dot '+(status(doc)==='error'?'danger':status(doc)==='processing'?'warning':'success'));
    let caption=s(doc.indexed?'indexed':'pending');if(status(doc)==='processing')caption=s(doc.stage==='indexing'?'indexing':doc.stage==='chunking'?'chunking':'queued');if(status(doc)==='error')caption=serverText(doc.error_info?.message)||s('error');if(doc.needs_reindex)caption=s('reindex_required_short');label.append(dot,node('span','',caption));stateCell.append(label);
    const updated=node('td','updated-time',relativeTime(doc.updated_at));updated.title=doc.updated_at?new Date(doc.updated_at).toLocaleString(intlLocale()):'';
    const actions=node('td');if(status(doc)==='error')actions.append(button(s('retry'),'btn btn--danger',()=>startOperation('/api/admin/index','index',{file:doc.name})));
    if(topics)wrap.prepend(topics.checkbox(doc.name));
    const topic=topics?.topics.find(item=>item.id===doc.topic_id);if(topic){const label=node('small','document-topic',topics.path(topic));label.title=topics.path(topic);name.append(label);}
    row.append(name,bytes,chunks,stateCell,updated,actions);$('documents-table').append(row);
  }
  $('library-empty').hidden=visible.length>0;$('library-empty').textContent=s(docs.length?'no_matches':'library_empty');$('page-label').textContent=state.page+' / '+pages;$('previous-page').disabled=state.page<=1;$('next-page').disabled=state.page>=pages;
  $('table-caption').textContent=s('table_caption',{shown:visible.length,total:filtered.length});
  topics?.selection(visible.map(doc=>doc.name));
}
function renderProcess(){
  const status=state.status;const job=(status?.jobs||[]).find((job)=>job.status==='running'&&['index','prepare'].includes(job.kind));
  const uploading=queue?.entries.find((entry)=>entry.state==='uploading');
  if(job)state.lastPhase={stage:job.stage,value:job.stage_progress??null,name:job.current_document||job.document_name||'',active:true};
  else if(uploading)state.lastPhase={stage:'upload',value:uploading.upload,name:uploading.name,active:true};
  else if(state.lastPhase)state.lastPhase={...state.lastPhase,active:false};
  const current=state.lastPhase;const stages=['upload','chunking','indexing'];const index=current?stages.indexOf(current.stage):-1;
  $('process-state').textContent=current?.active?(job?.document_names?.length>1?t("ui.documents_active_76b844", {value1: (job.documents?.length||0), value2: (job.document_names.length)}):t("ui.active_723858")):t("ui.idle_06423d");$('process-state').className=`status-pill ${current?.active?'processing':''}`;
  document.querySelectorAll('#process-steps li').forEach((step,i)=>{const complete=current&&(i<index||!current.active);const active=current?.active&&i===index;step.classList.toggle('active',Boolean(active));step.classList.toggle('complete',Boolean(complete));step.querySelector('.step-number').textContent=i+1;const bar=step.querySelector('progress');if(active&&current.value==null)bar.removeAttribute('value');else bar.value=active?(current.value||0):complete?100:0;bar.className='ochre';});
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
// The panel refresh spins until the answer arrives; while the panel is open it re-reads every 5 s.
$('refresh-button').addEventListener('click',()=>{const control=$('refresh-button');control.classList.add('is-busy');refreshStatus().finally(()=>control.classList.remove('is-busy'));});
let systemPanelTimer;$('system-popover').addEventListener('toggle',event=>{clearInterval(systemPanelTimer);if(event.newState==='open')systemPanelTimer=setInterval(refreshStatus,5000);});
$('prepare-button').addEventListener('click',async()=>{const control=$('prepare-button'),label=control.querySelector('[data-i18n]');control.setAttribute('aria-busy','true');label.textContent=s('preparing');try{await startOperation('/api/admin/prepare','prepare');}finally{control.removeAttribute('aria-busy');label.textContent=s('prepare');}});$('index-button').addEventListener('click',()=>startOperation('/api/admin/index','index'));
$('queue-result-button').addEventListener('click',()=>state.lastResult&&showResult(state.lastResult,$('queue-result-button')));
let searchTimer;$('document-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.search=$('document-search').value;state.page=1;renderDocuments();},150);});
$('document-filters').addEventListener('click',(event)=>{const control=event.target.closest('[data-filter]');if(control){state.filter=control.dataset.filter;state.page=1;renderDocuments();}});
$('previous-page').addEventListener('click',()=>{state.page--;renderDocuments();});$('next-page').addEventListener('click',()=>{state.page++;renderDocuments();});
async function loadSettings(){const result=await api('/api/settings');preferences=result.settings;modelList=result.models;settingsController.set(result);}
$('reindex-settings').addEventListener('click',()=>startOperation('/api/admin/index','index'));
let statisticsWindow=604800,statisticsPage=1,ingestionPage=1;
async function refreshStatistics(){
  const requestedWindow=statisticsWindow,requestedPage=statisticsPage,requestedIngestionPage=ingestionPage;
  try{
    const stats=await api(`/api/statistics?history_seconds=${requestedWindow}&page=${requestedPage}&ingestion_page=${requestedIngestionPage}${state.statisticsFilter?`&query_id=${encodeURIComponent(state.statisticsFilter)}`:''}`);
    if(requestedWindow!==statisticsWindow||requestedPage!==statisticsPage||requestedIngestionPage!==ingestionPage)return;
    statisticsPage=stats.page||1;$('statistics-scope').textContent=stats.scope;
    $('statistics-page').textContent=t("ui.page_of_queries_65312d", {value1: (statisticsPage), value2: (stats.pages||1), value3: (stats.queries)});
    $('statistics-previous').disabled=statisticsPage<=1;$('statistics-next').disabled=statisticsPage>=(stats.pages||1);
    $('statistics-totals').textContent=stats.queries+t("ui.queries_268864")+stats.completed+t("ui.completed_fed359")+stats.errors+t("ui.errors_3fe67e")+number(stats.output_tokens)+t("ui.output_tokens_ef3c29");
    $('visual-count').textContent=number(stats.visuals);$('statistics-queries').textContent=number(stats.queries);$('statistics-errors').textContent=number(stats.errors);$('statistics-output').textContent=number(stats.output_tokens);$('clear-trace-filter').hidden=!state.statisticsFilter;
    const ingestions=stats.ingestions||{total:0,page:1,pages:1,recent:[]};ingestionPage=ingestions.page;
    $('ingestion-history-info').textContent=t("ui.same_period_as_the_queries_actual_execution_durations_and_queue_w_4f2094");
    $('ingestion-page').textContent=t("ui.page_of_jobs_35be73", {value1: (ingestionPage), value2: (ingestions.pages), value3: (ingestions.total)});
    $('ingestion-previous').disabled=ingestionPage<=1;$('ingestion-next').disabled=ingestionPage>=ingestions.pages;
    $('ingestion-statistics').replaceChildren();
    for(const item of ingestions.recent){const row=node('tr');const names=item.document_names||[];const name=item.document_name||(names.length===1?names[0]:names.length?t("ui.documents_e66909", {value1: (names.length)}):t("ui.indexing_690a97"));
      for(const value of [name,item.elapsed_seconds==null?'—':duration(item.elapsed_seconds),item.queue_seconds==null?'—':duration(item.queue_seconds),number(item.chunks||0),{done:t("ui.completed_b14859"),error:t("ui.error_54a0e8"),running:t("ui.in_progress_75f1f7"),queued:t("ui.queued_d19761"),cancelled:t("ui.stopped_e5258c"),interrupted:t("ui.interrupted_ba95ed")}[item.status]||item.status])row.append(node('td','',value));
      if(names.length)row.children[0].title=names.join('\n');if(item.created_at)row.children[0].append(node('p','routing-reason',new Date(item.created_at).toLocaleString(intlLocale())));$('ingestion-statistics').append(row);}
    $('query-statistics').replaceChildren();const slowest=Math.max(1,...stats.recent.map(item=>item.elapsed_seconds||0));
    for(const item of stats.recent){
      const metrics=item.metrics||{},row=node('tr'),time=node('td','num',item.created_at?new Date(item.created_at).toLocaleTimeString(intlLocale()):'—'),question=node('td','',item.question),model=node('td','',metrics.model||'—'),route=node('td'),timing=node('td','num',item.elapsed_seconds==null?'—':number(item.elapsed_seconds,1)+' s');question.title=item.question;
      const mode=metrics.routing_mode==='search_only'?'retrieval_only':metrics.visual_mode==='images'?'visual':'text';route.append(node('span','badge '+(mode==='visual'?'badge--info':'badge--neutral'),s(mode)));route.title=serverText(metrics.routing_reason||'');
      const bar=node('span','bar'),fill=node('i');fill.style.width=100*(item.elapsed_seconds||0)/slowest+'%';bar.append(fill);timing.append(bar);if(item.queue_seconds>=1)timing.append(node('p','routing-reason',s('queue_wait',{value:duration(item.queue_seconds)})));
      row.dataset.query=item.id||'';row.append(time,question,model,route,timing,node('td','num',metrics.generation_tok_s==null?'—':number(metrics.generation_tok_s,2)),node('td','num',metrics.output_tokens==null?'—':number(metrics.output_tokens)),node('td','num',metrics.input_tokens==null?'—':number(metrics.input_tokens)));
      $('query-statistics').append(row);
    }
    if(!stats.recent.length){const row=node('tr');const cell=node('td','',t("ui.no_queries_saved_in_this_period_4fab7d"));cell.colSpan=8;row.append(cell);$('query-statistics').append(row);}
  }catch(error){toast(error.message);}
}
$('refresh-statistics').addEventListener('click',refreshStatistics);$('clear-trace-filter').addEventListener('click',()=>navigate('/estadisticas'));
$('statistics-history-range').addEventListener('click',event=>{const control=event.target.closest('[data-history-seconds]');if(!control)return;statisticsWindow=Number(control.dataset.historySeconds);statisticsPage=1;ingestionPage=1;$('statistics-history-range').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button===control)));refreshStatistics();});
$('statistics-previous').addEventListener('click',()=>{statisticsPage=Math.max(1,statisticsPage-1);refreshStatistics();});
$('statistics-next').addEventListener('click',()=>{statisticsPage++;refreshStatistics();});
$('ingestion-previous').addEventListener('click',()=>{ingestionPage=Math.max(1,ingestionPage-1);refreshStatistics();});
$('ingestion-next').addEventListener('click',()=>{ingestionPage++;refreshStatistics();});
navigate(location.pathname+location.search,true);
window.addEventListener('rag:languagechange',()=>{
  navigate(location.pathname+location.search,true);if(state.status)statusView(state.status);queue?.render();const scroll=$('chat-thread').scrollTop;chat?.render();$('chat-thread').scrollTop=scroll;serverConsole?.render();settingsController?.render();shell?.render();
  if(telemetry?.display){const shown=telemetry.display;telemetry.render(shown.data);if(telemetry.paused){telemetry.display=shown;telemetry.draw(shown);}}
  if(telemetry)$('hardware-pause').textContent=s(telemetry.paused?'resume':'pause');
  for(const id of ['settings-message','console-status','hardware-status'])$(id).textContent=serverText($(id).textContent);
  if(!$('toast').hidden)$('toast').querySelector('span').textContent=serverText($('toast').querySelector('span').textContent);
  if($('result-dialog').open&&state.lastResult)showResult(state.lastResult);
});
async function init(){
  try{
    state.token=(await api('/api/session')).token;
    shell=new Shell(api);shell.setView(state.view);settingsController=new SettingsController(api,(value,saved)=>{preferences=value;state.k=value.k;shell.setSettings(value);if(saved)refreshStatus();});
    serverConsole=new ServerConsole(api);serverConsole.setActive(state.view==='console');
    telemetry=new Telemetry(api);telemetry.setActive(state.view==='statistics');
    topics=new LibraryTopics(api,refreshStatus,()=>{state.page=1;renderDocuments();});
    queue=new UploadQueue({api,token:()=>state.token,completed:(result)=>completed(result),changed:()=>renderProcess()});
    chat=new Chat(api,()=>{if(state.view==='statistics')refreshStatistics();},()=>settingsController.flush());
    await loadSettings();
    await refreshStatus();chat.restore(state.status?.jobs||[]);
    setInterval(()=>{if(!document.hidden)refreshStatus();},2000);
    setInterval(()=>{if(!document.hidden&&state.view==='statistics')refreshStatistics();},15000);
  }catch(error){toast(error.message);}
}
init();

