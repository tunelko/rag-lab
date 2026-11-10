import {randomId} from './ids.js';
import {t, serverText} from './i18n.js';
import {$, node, icon, iconButton, button, fileType, progress, segments, errorData, number, size, terminal, toast, announce} from './ui.js';

const s=(key,values={})=>t(`sentinel.${key}`,values);
const STORE = 'rag-local-upload-queue-v2';
const labels = () => ({queued:t("ui.queued_d19761"), uploading:t("ui.uploading_8f2161"), uploaded:t("ui.uploaded_24762e"), fragmenting:t("ui.splitting_into_chunks_726c36"), indexing:t("ui.indexing_8df561"), indexed:t("ui.indexed_bb7f0d"), unindexed:t("ui.not_indexed_f84805"), error:t("ui.error_54a0e8"), rejected:t("ui.error_54a0e8"), cancelled:t("ui.cancelled_f40738"), interrupted:t("ui.interrupted_8da730")});
const activeStates = new Set(['queued','uploading','uploaded','fragmenting','indexing']);
const phaseClass = (state) => ['fragmenting','indexing','uploaded'].includes(state) ? 'processing' : ['error','rejected','interrupted'].includes(state) ? 'error' : state === 'indexed' ? 'success' : state === 'uploading' ? 'uploading' : '';

export class UploadQueue {
  constructor({api, token, completed, changed}) {
    this.api=api; this.token=token; this.completed=completed; this.changed=changed;
    this.entries=[]; this.running=false; this.paused=false; this.uploads=0; this.maxTotal=0; this.samples=[]; this.ignored=new Set(); this.notified=new Set(); this.batch=null; this.documents=[]; this.pollTimer=null;
    this.cancelledBatches=new Set();
    try {
      const saved=JSON.parse(sessionStorage.getItem(STORE)||'{}');
      this.cancelledBatches=new Set(saved.cancelledBatches||[]);
      this.entries=(saved.entries||[]).map((entry)=>{
        if (['queued','uploading'].includes(entry.state) && !entry.jobId) return {...entry,state:'interrupted',error:errorData({code:'NETWORK',message:t("ui.the_upload_was_interrupted_by_a_reload_add_the_file_again_1a934e")})};
        return entry;
      });
      this.ignored=new Set(saved.ignored||[]); this.notified=new Set(saved.notified||[]); this.batch=saved.batch||null; this.maxTotal=saved.maxTotal||0;
      this.running=Boolean(this.batch && !this.batch.finished && this.entries.some((entry)=>entry.jobId && activeStates.has(entry.state)));
    } catch { sessionStorage.removeItem(STORE); }
    $('select-files').addEventListener('click',()=>$('file-input').click());
    $('file-input').addEventListener('change',(event)=>{this.add(event.target.files); event.target.value='';});
    const drop=$('drop-zone');
    for(const name of ['dragenter','dragover']) drop.addEventListener(name,(event)=>{event.preventDefault();drop.classList.add('dragging');$('drop-title').textContent=t("ui.drop_to_add_to_the_queue_d5aa9b");});
    for(const name of ['dragleave','drop']) drop.addEventListener(name,(event)=>{event.preventDefault();drop.classList.remove('dragging');$('drop-title').textContent=t("ui.drop_your_documents_here_403059");if(name==='drop')this.add(event.dataTransfer.files);});
    $('upload-button').addEventListener('click',()=>this.start());
    $('pause-queue').addEventListener('click',()=>{this.paused=!this.paused;this.schedule();this.emit();});
    $('clear-queue').addEventListener('click',()=>{this.entries.filter((entry)=>['indexed','cancelled'].includes(entry.state)).forEach((entry)=>{if(entry.jobId)this.ignored.add(entry.jobId);});this.entries=this.entries.filter((entry)=>!['indexed','cancelled'].includes(entry.state));this.emit();toast(t("ui.completed_entries_cleared_from_the_queue_0a5e8c"));});
    $('cancel-pending').addEventListener('click',()=>this.cancelAll());
    $('auto-index').addEventListener('change',()=>this.emit());
    $('upload-topic').addEventListener('change',()=>{for(const entry of this.entries)if(entry.state==='queued'&&!entry.batchId)entry.topicId=$('upload-topic').value||null;this.emit();});
    this.render(); this.poll();
  }
  serverDocument(entry){
    return this.documents.find(document=>entry.documentId?document.document_id===entry.documentId:entry.serverName?document.name===entry.serverName:document.name===entry.name&&document.bytes===entry.size);
  }
  recover(entry){
    const candidate=entry.error?.code==='NETWORK'||entry.state==='interrupted'||(!entry.jobId&&['uploaded','fragmenting','indexing'].includes(entry.state));
    const document=candidate&&this.serverDocument(entry);
    if(!document)return false;
    entry.serverName=document.name;entry.documentId=document.document_id;
    if(!document.indexed||(document.status&&document.status!=='indexed'))return false;
    entry.state='indexed';entry.upload=100;entry.process=100;entry.stageProgress=100;entry.chunks=document.chunks||0;
    entry.error=null;entry.cancelRequested=false;entry.cancellable=false;entry.recovered=true;
    return true;
  }
  setDocuments(documents){
    this.documents=documents;const previous=this.entries.map(entry=>entry.serverName);const recovered=this.entries.filter(entry=>this.recover(entry));
    if(recovered.length||this.entries.some((entry,index)=>entry.serverName!==previous[index]))this.emit();
    if(recovered.length){if(this.running)this.finishCheck();else if(this.batch&&recovered.some(entry=>this.batch.ids.includes(entry.uid))){this.batch.finished=true;this.completed(this.result());}}
  }
  add(files){
    const list=[...files]; let valid=0;
    if(list.length>20)toast(t("ui.skipped_files_a_batch_can_contain_at_most_20_01fc90", {value1: (list.length-20)}));
    if(!this.running&&!this.entries.some((entry)=>!entry.batchId)){this.maxTotal=0;this.samples=[];}
    for(const file of list.slice(0,20)){
      const entry={uid:randomId(),name:file.name,size:file.size,file,state:'queued',upload:0,process:0,stageProgress:0,chunks:0,batchId:this.running?this.batch.id:null,autoIndex:$('auto-index').checked,topicId:$('upload-topic').value||null};
      const extension='.'+(file.name.split('.').pop()||'').toLowerCase();
      if(!['.pdf','.txt','.md','.markdown','.png','.jpg','.jpeg','.webp','.zip'].includes(extension))entry.error=errorData({code:'UNSUPPORTED_TYPE',message:t("ui.unsupported_format_b5e884", {value1: (extension)})});
      else if(file.size>20*1024*1024)entry.error=errorData({code:'FILE_TOO_LARGE',message:t("ui.exceeds_the_20_mb_file_limit_eab388", {value1: (size(file.size))})});
      if(entry.error){entry.state='rejected';announce(t("ui.failed_1c52f6", {value1: (entry.name)}));}else valid++;
      this.entries.push(entry); if(this.running)this.batch.ids.push(entry.uid);
    }
    if(!valid&&list.length){$('drop-zone').classList.add('rejected');$('drop-title').textContent=t("ui.no_files_are_supported_pdf_txt_or_markdown_1d8fba");setTimeout(()=>{$('drop-zone').classList.remove('rejected');$('drop-title').textContent=t("ui.drop_your_documents_here_403059");},3000);}
    this.emit(); if(this.running)this.schedule();
  }
  start(){
    if(this.running){if(this.paused){this.paused=false;this.schedule();}return;}
    const fresh=this.entries.filter((entry)=>!entry.batchId&&entry.state!=='cancelled');
    if(!fresh.length)return;
    this.batch={id:randomId(),ids:fresh.map((entry)=>entry.uid),started:Date.now(),finished:false};
    for(const entry of fresh){entry.batchId=this.batch.id;entry.autoIndex=$('auto-index').checked;}
    this.running=true;this.paused=false;this.maxTotal=0;this.samples=[];this.schedule();this.emit();this.finishCheck();
  }
  schedule(){
    if(!this.running||this.paused)return;
    while(this.uploads<2){const next=this.entries.find((entry)=>entry.state==='queued'&&entry.batchId===this.batch.id);if(!next)break;this.upload(next);}
    this.ensurePoll();
  }
  upload(entry){
    entry.state='uploading';entry.started=performance.now();entry.samples=[];this.uploads++;
    const xhr=new XMLHttpRequest();entry.xhr=xhr;xhr.open('POST','/api/admin/upload');xhr.setRequestHeader('X-RAG-Token',this.token());xhr.timeout=600000;
    xhr.upload.onprogress=(event)=>{if(event.lengthComputable){entry.upload=100*event.loaded/event.total;entry.loaded=entry.size*event.loaded/event.total;entry.samples.push({time:performance.now(),bytes:entry.loaded});entry.samples=entry.samples.filter((sample)=>sample.time>=performance.now()-3000);this.emit();}};
    let settled=false;
    const settledOnce=()=>{if(settled)return false;settled=true;this.uploads--;entry.xhr=null;this.schedule();this.ensurePoll();return true;};
    xhr.onload=()=>{
      if(!settledOnce())return;
      let result;try{result=JSON.parse(xhr.responseText);}catch{this.fail(entry,{code:'NETWORK',detail:xhr.responseText});return;}
      if(xhr.status===403&&!entry.authRetried&&typeof result.detail==='string'&&result.detail.includes('Recarga esta página')){
        entry.authRetried=true;this.api('/api/session').then(()=>{if(entry.state==='cancelled'||this.cancelledBatches.has(entry.batchId)){entry.state='cancelled';this.finishCheck();}else{entry.state='queued';this.schedule();}this.emit();}).catch(error=>this.fail(entry,error.info||{code:'NETWORK'}));return;
      }
      if(xhr.status<200||xhr.status>=300){this.fail(entry,result.error_info||{message:typeof result.detail==='string'?result.detail:undefined,detail:JSON.stringify(result.detail)});return;}
      entry.upload=100;entry.serverName=result.saved?.[0]||entry.name;entry.documentId=result.document_id;entry.jobId=result.job_id;
      entry.state=entry.jobId?'uploaded':'unindexed';entry.process=entry.jobId?0:100;
      announce(`${entry.name} subido`);this.emit();this.ensurePoll();this.finishCheck();
    };
    xhr.onerror=()=>{if(settledOnce())this.fail(entry,{code:'NETWORK'});};xhr.ontimeout=xhr.onerror;
    xhr.onabort=()=>{if(settledOnce()){entry.state='cancelled';this.emit();this.finishCheck();}};
    const body=new FormData();body.append('files',entry.file);body.append('auto_index',String(entry.autoIndex));body.append('batch_id',entry.batchId);if(entry.topicId)body.append('topic_id',entry.topicId);xhr.send(body);this.emit();
  }
  fail(entry,error){entry.state='error';entry.error=errorData(error);announce(t("ui.failed_1c52f6", {value1: (entry.name)}));this.emit();this.finishCheck();}
  async cancelAll(){
    if(this.cancelling)return;
    this.cancelling=true;this.paused=true;this.emit();
    for(const entry of this.entries.filter(entry=>activeStates.has(entry.state)))if(entry.batchId)this.cancelledBatches.add(entry.batchId);
    this.cancelWatchUntil=Date.now()+15000;
    let finishingWrite=false;
    try{
      for(const entry of this.entries.filter(entry=>['queued','uploading'].includes(entry.state)))await this.cancel(entry);
      for(let pass=0;pass<3;pass++){
        if(pass)await new Promise(resolve=>setTimeout(resolve,250));
        const result=await this.api('/api/jobs?active=1');this.serverJobs=result.jobs||[];
        const jobs=this.serverJobs.filter(job=>job.kind==='index').sort((a,b)=>(a.status==='queued'?0:1)-(b.status==='queued'?0:1));
        for(const job of jobs){
          if(job.batch_id)this.cancelledBatches.add(job.batch_id);
          if(job.status==='running'&&job.stage==='indexing'){finishingWrite=true;continue;}
          const cancelled=await this.api(`/api/jobs/${job.id}`,{method:'DELETE'});
          Object.assign(job,cancelled);
          const entry=this.entries.find(item=>item.jobId===job.id);if(entry)this.applyJob(entry,cancelled);
        }
      }
      toast(finishingWrite?t("ui.pending_jobs_cancelled_the_current_index_write_will_finish_before_788d3a"):t("ui.cancellation_requested_files_and_cache_are_preserved_f126f5"));
      this.ensurePoll();
    }catch(error){toast(error.message);}
    finally{this.cancelling=false;this.emit();}
  }
  async cancel(entry){
    if(entry.state==='queued'){entry.state='cancelled';this.emit();this.finishCheck();return;}
    if(entry.state==='uploading'){if(entry.xhr)entry.xhr.abort();else{entry.state='cancelled';entry.cancelRequested=true;this.emit();this.finishCheck();}return;}
    if(entry.jobId&&entry.cancellable!==false){try{entry.cancelRequested=true;const job=await this.api(`/api/jobs/${entry.jobId}`,{method:'DELETE'});this.applyJob(entry,job);this.emit();this.ensurePoll();}catch(error){toast(error.message);entry.cancelRequested=false;}}
  }
  remove(entry){if(entry.jobId)this.ignored.add(entry.jobId);this.entries=this.entries.filter((item)=>item.uid!==entry.uid);this.emit();toast(t("ui.file_removed_from_the_queue_5af5f7"));}
  async retry(entry){
    if(entry.error&&!entry.error.retryable)return;
    try{this.setDocuments((await this.api('/api/status')).documents||[]);if(entry.state==='indexed')return;}catch{}
    entry.error=null;entry.authRetried=false;entry.cancelRequested=false;entry.process=0;entry.jobId=null;entry.stageProgress=0;entry.batchId=null;
    if(entry.serverName){
      entry.state='uploaded';entry.upload=100;
      if(!this.running){this.batch={id:randomId(),ids:[entry.uid],started:Date.now(),finished:false};this.running=true;this.maxTotal=0;this.samples=[];}else this.batch.ids.push(entry.uid);
      entry.batchId=this.batch.id;
      try{const result=await this.api('/api/admin/index',{method:'POST',body:JSON.stringify({file:entry.serverName}),headers:{'Content-Type':'application/json'}});entry.jobId=result.job_id;this.emit();this.ensurePoll();}catch(error){this.fail(entry,error.info||{code:'NETWORK',detail:error.message});}
    }else if(entry.file){entry.state='queued';entry.upload=0;if(this.running){entry.batchId=this.batch.id;this.batch.ids.push(entry.uid);this.schedule();}else this.start();this.emit();}
    else{entry.state='interrupted';entry.error=errorData({code:'NETWORK',message:t("ui.add_the_file_again_to_retry_22db07")});this.emit();$('file-input').click();}
  }
  applyJob(entry,job){
    const previous=entry.state;
    if(job.recovered)entry.recovered=true;
    entry.pipelineStage=job.stage;entry.jobId=job.id;entry.process=job.progress??entry.process;entry.stageProgress=job.stage_progress??0;entry.chunks=job.chunks||0;entry.cancellable=job.cancellable;entry.cancelRequested=['queued','running'].includes(job.status)&&Boolean(job.cancel_requested);
    entry.serverName=entry.serverName||job.document_name||entry.name;
    if(job.status==='done'){entry.state='indexed';entry.error=null;entry.upload=100;entry.process=100;const own=(job.documents||[]).find((item)=>item.name===entry.serverName.split('/').pop());entry.chunks=own?.chunks??job.chunks??0;entry.skipped=own?.skipped||false;}
    else if(job.status==='error'){entry.state='error';entry.error=errorData(job.error_info||(typeof job.error==='object'?job.error:{message:job.error}));}
    else if(job.status==='cancelled')entry.state='cancelled';
    else if(job.status==='queued')entry.state='uploaded';
    else entry.state=job.stage==='indexing'?'indexing':'fragmenting';
    if(previous!==entry.state&&['indexed','error'].includes(entry.state))announce(entry.state==='indexed'?t("ui.indexed_chunks_c6c0b1", {value1: (entry.name), value2: (number(entry.chunks))}):t("ui.failed_1c52f6", {value1: (entry.name)}));
  }
  ensurePoll(){if(!this.pollTimer)this.pollTimer=setTimeout(()=>this.poll(),1000);}
  async poll(){
    clearTimeout(this.pollTimer);this.pollTimer=null;if(this.polling)return;this.polling=true;
    try{
      const result=await this.api('/api/jobs?active=1');this.serverJobs=result.jobs||[];const jobs=[...this.serverJobs,...(result.recent||[])];
      for(const job of jobs){
        if(job.kind!=='index'||!job.document_name||this.ignored.has(job.id))continue;
        if(this.cancelledBatches.has(job.batch_id)&&['queued','running'].includes(job.status)&&!job.cancel_requested&&job.stage!=='indexing'){
          try{Object.assign(job,await this.api(`/api/jobs/${job.id}`,{method:'DELETE'}));}catch(error){toast(error.message);}
        }
        let entry=this.entries.find((item)=>item.jobId===job.id);
        if(!entry&&this.cancelledBatches.has(job.batch_id))continue;
        if(!entry&&['queued','running','processing'].includes(job.status)){
          entry={uid:randomId(),name:job.document_name,size:job.bytes||1,upload:100,process:job.progress||0,state:'uploaded',jobId:job.id,batchId:job.batch_id||job.id,serverName:job.document_name,autoIndex:true};this.entries.push(entry);
          if(!this.running){this.batch={id:entry.batchId,ids:[entry.uid],started:Date.parse(job.created_at)||Date.now(),finished:false};this.running=true;}else this.batch.ids.push(entry.uid);
        }
        if(entry)this.applyJob(entry,job);
      }
      const known=new Set(jobs.map((job)=>job.id));
      for(const entry of this.entries.filter((entry)=>entry.jobId&&activeStates.has(entry.state)&&!known.has(entry.jobId))){try{this.applyJob(entry,await this.api(`/api/jobs/${entry.jobId}`));}catch(error){if(error.status===404){
        this.documents=(await this.api('/api/status')).documents||[];
        entry.jobId=null;
        if(!this.recover(entry)){entry.state='interrupted';entry.cancelRequested=false;entry.error=errorData({code:'NETWORK',message:entry.serverName?t("ui.the_file_is_still_uploaded_retry_indexing_00d8ad"):t("ui.the_upload_could_not_be_confirmed_after_restarting_check_the_libr_5f713f")});entry.jobId=null;}
      }}}
      this.emit();this.finishCheck();
    }catch(error){if(this.entries.some((entry)=>entry.jobId&&activeStates.has(entry.state)))toast(t("ui.the_connection_to_the_local_server_was_lost_8caf59"));}
    finally{this.polling=false;if(this.entries.some((entry)=>entry.jobId&&activeStates.has(entry.state))||this.uploads||Date.now()<(this.cancelWatchUntil||0)||(this.serverJobs||[]).some(job=>job.kind==='index'&&['queued','running'].includes(job.status)))this.ensurePoll();}
  }
  currentEntries(){const fresh=this.entries.filter((entry)=>!entry.batchId);if(!this.running&&fresh.length)return fresh;return this.batch?this.entries.filter((entry)=>this.batch.ids.includes(entry.uid)):this.entries;}
  value(entry){if(['indexed','unindexed'].includes(entry.state))return 100;if(['uploaded','fragmenting','indexing'].includes(entry.state))return 40+.6*(entry.process||0);return .4*(entry.upload||0);}
  result(){
    const entries=this.currentEntries();
    return {kind:'batch',successes:entries.filter((entry)=>['indexed','unindexed'].includes(entry.state)).map((entry)=>({name:entry.name,chunks:entry.chunks||0,skipped:entry.skipped,recovered:entry.recovered})),failures:entries.filter((entry)=>['error','rejected','interrupted'].includes(entry.state)).map((entry)=>({name:entry.name,error:entry.error,retry:()=>this.retry(entry),remove:()=>this.remove(entry)})),unindexed:entries.some((entry)=>entry.state==='unindexed'),duration:(Date.now()-(this.batch?.started||Date.now()))/1000,retryAll:()=>entries.filter((entry)=>entry.error?.retryable).forEach((entry)=>this.retry(entry))};
  }
  finishCheck(){
    if(!this.running)return;const entries=this.currentEntries();
    if(entries.length&&entries.every((entry)=>terminal.has(entry.state))){this.running=false;this.batch.finished=true;this.emit();if(!this.notified.has(this.batch.id)){this.notified.add(this.batch.id);const result=this.result();this.save();if(result.successes.length||result.failures.length)this.completed(result);else toast(t("ui.queue_cancelled_0a7af3"));}}
  }
  save(){const entries=this.entries.map(({file,xhr,samples,...entry})=>entry);sessionStorage.setItem(STORE,JSON.stringify({entries,ignored:[...this.ignored],notified:[...this.notified],cancelledBatches:[...this.cancelledBatches],batch:this.batch,maxTotal:this.maxTotal}));}
  emit(){this.save();if(!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=null;this.render();this.save();this.changed?.();});}
  render(){
    $('queue-section').hidden=!this.entries.length;
    const completed=this.entries.filter((entry)=>['indexed','unindexed'].includes(entry.state)&&!entry.skipped).length;const unchanged=this.entries.filter(entry=>entry.skipped).length;const errors=this.entries.filter((entry)=>['error','rejected','interrupted'].includes(entry.state)).length;
    $('queue-caption').textContent=s('batch_summary',{indexed:completed,unchanged,errors});
    const current=this.currentEntries();const valid=current.filter((entry)=>!['rejected','cancelled'].includes(entry.state));const total=valid.reduce((sum,entry)=>sum+Math.max(1,entry.size),0);
    const percent=total?valid.reduce((sum,entry)=>sum+this.value(entry)*Math.max(1,entry.size),0)/total:0;this.maxTotal=Math.max(this.maxTotal,percent);
    $('total-percent').textContent=`${number(this.maxTotal)} %`;$('total-progress').setAttribute('aria-valuenow',String(Math.round(this.maxTotal)));
    const widths=[0,0,0];for(const entry of valid){const group=['indexed','unindexed'].includes(entry.state)?0:entry.state==='uploading'?2:1;widths[group]+=this.value(entry)*Math.max(1,entry.size)/(total||1);}const sum=widths.reduce((a,b)=>a+b,0);if(sum>=this.maxTotal-.01)this.lastSegments=widths;segments($('total-progress'),this.lastSegments||widths);
    const now=performance.now();this.samples.push({time:now,value:this.maxTotal});this.samples=this.samples.filter((sample)=>sample.time>=now-3000);
    const first=this.samples[0];const span=first?(now-first.time)/1000:0;const rate=span?(this.maxTotal-first.value)/span:0;
    $('total-eta').textContent=this.maxTotal>=100?t("ui.completed_856641"):span>=2&&rate>0?t("ui.approximately_s_remaining_4e4750", {value1: (Math.ceil((100-this.maxTotal)/rate))}):t("ui.calculating_bef25b");
    const focused=document.activeElement?.closest('[data-queue-action]');const focusKey=focused?.dataset.queueAction;
    $('upload-queue').replaceChildren();
    for(const entry of this.entries){
      const row=node('li','queue-row');row.dataset.state=entry.state;row.dataset.uploadProgress=String(entry.upload||0);row.dataset.totalProgress=String(this.value(entry));const type=fileType(entry.name);type.classList.add('queue-file-icon');row.append(type);
      const body=node('div','queue-body');const line=node('div','queue-line');line.append(node('span','queue-name',entry.name),node('span',`status-pill ${entry.skipped?'info':phaseClass(entry.state)}`,entry.cancelRequested&&activeStates.has(entry.state)?t("ui.cancelling_c5b0af"):entry.skipped?s('unchanged'):labels()[entry.state]));body.append(line);
      if(!['error','rejected','cancelled','interrupted'].includes(entry.state)){
        if(!entry.skipped){const phases=node('div','stages'),caption=node('div','stage-labels');const processing=['uploaded','fragmenting','indexing','indexed'].includes(entry.state),chunking=entry.pipelineStage==='chunking',writing=entry.state==='indexing',done=['indexed','unindexed'].includes(entry.state);
          const flags=[{done:processing||done,run:entry.state==='uploading'},{done:chunking||writing||done,run:entry.state==='fragmenting'&&!chunking},{done:writing||done,run:chunking},{done:done,run:writing},{done:done,run:writing}];
          ['stage_upload','stage_read','stage_chunks','stage_embed','stage_index'].forEach((key,index)=>{const stage=node('span',flags[index].done?'is-done':flags[index].run?'is-run':'');stage.title=s(key)+(index>=3&&writing?' · '+s('combined_stage'):'');phases.append(stage);caption.append(node('span','',s(key)));});body.append(phases,caption);}
      }
      let meta=size(entry.size);if(entry.state==='indexed')meta+=' · '+(entry.skipped?s('unchanged'):number(entry.chunks)+' '+s('chunks'));else if(entry.state==='unindexed')meta+=' · '+s('not_indexed');else if(entry.state==='uploading')meta+=' · '+number(entry.upload||0)+' %';
      const foot=node('div','queue-meta');foot.append(node('span','',meta),node('span','mono',`${number(this.value(entry))} %`));body.append(foot);
      if(entry.error)body.append(node('p','queue-error-text',serverText(entry.error.message)));row.append(body);
      const actions=node('div','queue-row-actions');const add=(control,key)=>{control.dataset.queueAction=`${entry.uid}:${key}`;actions.append(control);};
      if(['queued','uploading','uploaded','fragmenting'].includes(entry.state)){const control=iconButton('x',t("ui.cancel_8ce354", {value1: (entry.name)}),()=>this.cancel(entry));control.disabled=entry.cancellable===false||entry.cancelRequested;add(control,'cancel');}
      if(entry.state==='unindexed')add(button(t("ui.index_now_81cb03"),'button secondary',()=>this.retry(entry)), 'index');
      if(['error','interrupted'].includes(entry.state)&&entry.error?.retryable)add(button(entry.file||entry.serverName?t("ui.retry_a9254c"):t("ui.add_again_3a79a4"),'button error-outline',()=>this.retry(entry)), 'retry');
      if(terminal.has(entry.state))add(iconButton('x',t("ui.remove_615174", {value1: (entry.name)}),()=>this.remove(entry)), 'remove');
      row.append(actions);$('upload-queue').append(row);
    }
    if(focusKey&&(!document.activeElement||document.activeElement===document.body)){const target=[...$('upload-queue').querySelectorAll('[data-queue-action]')].find((element)=>element.dataset.queueAction===focusKey);(target||$('select-files')).focus({preventScroll:true});}
    const pending=this.entries.filter((entry)=>entry.state==='queued');const fresh=this.entries.filter((entry)=>!entry.batchId&&entry.state!=='cancelled');
    $('upload-reason').textContent=!fresh.length&&!this.running?s('queue_empty_reason'):'';
    $('upload-button').disabled=!this.running&&!fresh.length;$('upload-button').setAttribute('aria-busy',String(this.running));
    $('upload-button').querySelector('span').textContent=this.running?t("ui.of_d33efc", {value1: (this.paused?t("ui.paused_c28d81"):t("ui.processing_d9ab8c")), value2: (current.filter((entry)=>terminal.has(entry.state)).length), value3: (current.length)}):`${$('auto-index').checked?t("ui.upload_and_index_aa0b2b"):t("ui.upload_b3aad4")} (${fresh.length})`;
    $('pause-queue').textContent=this.paused?t("ui.resume_5685e1"):t("ui.pause_cd25ee");$('pause-queue').disabled=!this.running;
    const localWork=this.entries.some(entry=>['queued','uploading'].includes(entry.state));
    const serverWork=(this.serverJobs||[]).some(job=>job.kind==='index'&&['queued','running'].includes(job.status)&&!job.cancel_requested&&(job.status==='queued'||job.cancellable!==false));
    $('cancel-pending').disabled=this.cancelling||(!localWork&&!serverWork);$('cancel-pending').textContent=this.cancelling?t("ui.cancelling_c5b0af"):t("ui.cancel_processing_141faf");
    $('clear-queue').disabled=!this.entries.some((entry)=>['indexed','cancelled'].includes(entry.state));
    const count=this.entries.filter((entry)=>activeStates.has(entry.state)).length;window.dispatchEvent(new CustomEvent('rag:queue',{detail:{pending:count,uploading:this.entries.some(entry=>entry.state==='uploading'),percent:this.maxTotal}}));
  }
}
