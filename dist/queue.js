import {$, node, icon, iconButton, button, fileType, progress, segments, errorData, number, size, terminal, toast, announce} from './ui.js';

const STORE = 'rag-local-upload-queue-v2';
const labels = {queued:'En cola', uploading:'Subiendo', uploaded:'Subido', fragmenting:'Fragmentando', indexing:'Indexando', indexed:'Indexado', unindexed:'Sin indexar', error:'Error', rejected:'Error', cancelled:'Cancelado', interrupted:'Interrumpido'};
const activeStates = new Set(['queued','uploading','uploaded','fragmenting','indexing']);
const phaseClass = (state) => ['fragmenting','indexing','uploaded'].includes(state) ? 'processing' : ['error','rejected','interrupted'].includes(state) ? 'error' : state === 'indexed' ? 'success' : state === 'uploading' ? 'uploading' : '';

export class UploadQueue {
  constructor({api, token, completed, changed}) {
    this.api=api; this.token=token; this.completed=completed; this.changed=changed;
    this.entries=[]; this.running=false; this.paused=false; this.uploads=0; this.maxTotal=0; this.samples=[]; this.ignored=new Set(); this.notified=new Set(); this.batch=null; this.documents=[]; this.pollTimer=null;
    try {
      const saved=JSON.parse(sessionStorage.getItem(STORE)||'{}');
      this.entries=(saved.entries||[]).map((entry)=>{
        if (['queued','uploading'].includes(entry.state) && !entry.jobId) return {...entry,state:'interrupted',error:errorData({code:'NETWORK',message:'La subida se interrumpió al recargar. Vuelve a añadir el archivo.'})};
        return entry;
      });
      this.ignored=new Set(saved.ignored||[]); this.notified=new Set(saved.notified||[]); this.batch=saved.batch||null; this.maxTotal=saved.maxTotal||0;
      this.running=Boolean(this.batch && !this.batch.finished && this.entries.some((entry)=>entry.jobId && activeStates.has(entry.state)));
    } catch { sessionStorage.removeItem(STORE); }
    $('select-files').addEventListener('click',()=>$('file-input').click());
    $('file-input').addEventListener('change',(event)=>{this.add(event.target.files); event.target.value='';});
    const drop=$('drop-zone');
    for(const name of ['dragenter','dragover']) drop.addEventListener(name,(event)=>{event.preventDefault();drop.classList.add('dragging');$('drop-title').textContent='Suelta para añadir a la cola';});
    for(const name of ['dragleave','drop']) drop.addEventListener(name,(event)=>{event.preventDefault();drop.classList.remove('dragging');$('drop-title').textContent='Arrastra tus documentos aquí';if(name==='drop')this.add(event.dataTransfer.files);});
    $('upload-button').addEventListener('click',()=>this.start());
    $('pause-queue').addEventListener('click',()=>{this.paused=!this.paused;this.schedule();this.emit();});
    $('clear-queue').addEventListener('click',()=>{this.entries.filter((entry)=>['indexed','cancelled'].includes(entry.state)).forEach((entry)=>{if(entry.jobId)this.ignored.add(entry.jobId);});this.entries=this.entries.filter((entry)=>!['indexed','cancelled'].includes(entry.state));this.emit();toast('Completados eliminados de la cola');});
    $('cancel-pending').addEventListener('click',()=>{const entries=this.entries.filter((entry)=>['queued','uploading'].includes(entry.state));if(entries.length>3&&!confirm(`¿Cancelar los ${entries.length} archivos pendientes?`))return;entries.forEach((entry)=>this.cancel(entry));});
    $('auto-index').addEventListener('change',()=>this.emit());
    this.render(); this.poll();
  }
  setDocuments(documents){this.documents=documents;}
  add(files){
    const list=[...files]; let valid=0;
    if(list.length>20)toast(`Se han omitido ${list.length-20} archivos: el máximo por lote es 20.`);
    if(!this.running&&!this.entries.some((entry)=>!entry.batchId)){this.maxTotal=0;this.samples=[];}
    for(const file of list.slice(0,20)){
      const entry={uid:crypto.randomUUID(),name:file.name,size:file.size,file,state:'queued',upload:0,process:0,stageProgress:0,chunks:0,batchId:this.running?this.batch.id:null,autoIndex:$('auto-index').checked};
      const extension='.'+(file.name.split('.').pop()||'').toLowerCase();
      if(!['.pdf','.txt','.md','.markdown'].includes(extension))entry.error=errorData({code:'UNSUPPORTED_TYPE',message:`Formato no compatible (${extension}).`});
      else if(file.size>20*1024*1024)entry.error=errorData({code:'FILE_TOO_LARGE',message:`Supera el límite de 20 MB por archivo (${size(file.size)}).`});
      else if(this.documents.some((doc)=>doc.indexed&&doc.name.toLowerCase()===file.name.toLowerCase()&&doc.bytes===file.size)||this.entries.some((item)=>!['error','rejected','cancelled','interrupted'].includes(item.state)&&item.name.toLowerCase()===file.name.toLowerCase()&&item.size===file.size))entry.error=errorData({code:'DUPLICATE'});
      if(entry.error){entry.state='rejected';announce(`${entry.name} con error`);}else valid++;
      this.entries.push(entry); if(this.running)this.batch.ids.push(entry.uid);
    }
    if(!valid&&list.length){$('drop-zone').classList.add('rejected');$('drop-title').textContent='Ningún archivo es compatible (PDF, TXT o Markdown)';setTimeout(()=>{$('drop-zone').classList.remove('rejected');$('drop-title').textContent='Arrastra tus documentos aquí';},3000);}
    this.emit(); if(this.running)this.schedule();
  }
  start(){
    if(this.running){if(this.paused){this.paused=false;this.schedule();}return;}
    const fresh=this.entries.filter((entry)=>!entry.batchId&&entry.state!=='cancelled');
    if(!fresh.length)return;
    this.batch={id:crypto.randomUUID(),ids:fresh.map((entry)=>entry.uid),started:Date.now(),finished:false};
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
      if(xhr.status<200||xhr.status>=300){this.fail(entry,result.error_info||{message:typeof result.detail==='string'?result.detail:undefined,detail:JSON.stringify(result.detail)});return;}
      entry.upload=100;entry.serverName=result.saved?.[0]||entry.name;entry.documentId=result.document_id;entry.jobId=result.job_id;
      entry.state=entry.jobId?'uploaded':'unindexed';entry.process=entry.jobId?0:100;
      announce(`${entry.name} subido`);this.emit();this.ensurePoll();this.finishCheck();
    };
    xhr.onerror=()=>{if(settledOnce())this.fail(entry,{code:'NETWORK'});};xhr.ontimeout=xhr.onerror;
    xhr.onabort=()=>{if(settledOnce()){entry.state='cancelled';this.emit();this.finishCheck();}};
    const body=new FormData();body.append('files',entry.file);body.append('auto_index',String(entry.autoIndex));body.append('batch_id',entry.batchId);xhr.send(body);this.emit();
  }
  fail(entry,error){entry.state='error';entry.error=errorData(error);announce(`${entry.name} con error`);this.emit();this.finishCheck();}
  async cancel(entry){
    if(entry.state==='queued'){entry.state='cancelled';this.emit();this.finishCheck();return;}
    if(entry.state==='uploading'){entry.xhr?.abort();return;}
    if(entry.jobId&&entry.cancellable!==false){try{entry.cancelRequested=true;const job=await this.api(`/api/jobs/${entry.jobId}`,{method:'DELETE'});this.applyJob(entry,job);this.emit();this.ensurePoll();}catch(error){toast(error.message);entry.cancelRequested=false;}}
  }
  remove(entry){if(entry.jobId)this.ignored.add(entry.jobId);this.entries=this.entries.filter((item)=>item.uid!==entry.uid);this.emit();toast('Archivo quitado de la cola');}
  async retry(entry){
    if(entry.error&&!entry.error.retryable)return;
    entry.error=null;entry.cancelRequested=false;entry.process=0;entry.jobId=null;entry.stageProgress=0;entry.batchId=null;
    if(entry.serverName){
      entry.state='uploaded';entry.upload=100;
      if(!this.running){this.batch={id:crypto.randomUUID(),ids:[entry.uid],started:Date.now(),finished:false};this.running=true;this.maxTotal=0;this.samples=[];}else this.batch.ids.push(entry.uid);
      entry.batchId=this.batch.id;
      try{const result=await this.api('/api/admin/index',{method:'POST',body:JSON.stringify({file:entry.serverName}),headers:{'Content-Type':'application/json'}});entry.jobId=result.job_id;this.emit();this.ensurePoll();}catch(error){this.fail(entry,error.info||{code:'NETWORK',detail:error.message});}
    }else if(entry.file){entry.state='queued';entry.upload=0;if(this.running){entry.batchId=this.batch.id;this.batch.ids.push(entry.uid);this.schedule();}else this.start();this.emit();}
    else{entry.state='interrupted';entry.error=errorData({code:'NETWORK',message:'Vuelve a añadir el archivo para reintentar.'});this.emit();$('file-input').click();}
  }
  applyJob(entry,job){
    const previous=entry.state;
    entry.jobId=job.id;entry.process=job.progress??entry.process;entry.stageProgress=job.stage_progress??0;entry.chunks=job.chunks||0;entry.cancellable=job.cancellable;entry.cancelRequested=job.cancel_requested;
    entry.serverName=entry.serverName||job.document_name||entry.name;
    if(job.status==='done'){entry.state='indexed';entry.upload=100;entry.process=100;const own=(job.documents||[]).find((item)=>item.name===entry.serverName.split('/').pop());entry.chunks=own?.chunks??job.chunks??0;entry.skipped=own?.skipped||false;}
    else if(job.status==='error'){entry.state='error';entry.error=errorData(job.error_info||(typeof job.error==='object'?job.error:{message:job.error}));}
    else if(job.status==='cancelled')entry.state='cancelled';
    else if(job.status==='queued')entry.state='uploaded';
    else entry.state=job.stage==='indexing'?'indexing':'fragmenting';
    if(previous!==entry.state&&['indexed','error'].includes(entry.state))announce(entry.state==='indexed'?`${entry.name} indexado, ${number(entry.chunks)} fragmentos`:`${entry.name} con error`);
  }
  ensurePoll(){if(!this.pollTimer)this.pollTimer=setTimeout(()=>this.poll(),1000);}
  async poll(){
    clearTimeout(this.pollTimer);this.pollTimer=null;if(this.polling)return;this.polling=true;
    try{
      const result=await this.api('/api/jobs?active=1');const jobs=[...(result.jobs||[]),...(result.recent||[])];
      for(const job of jobs){
        if(job.kind!=='index'||!job.document_name||this.ignored.has(job.id))continue;
        let entry=this.entries.find((item)=>item.jobId===job.id);
        if(!entry&&['queued','running','processing'].includes(job.status)){
          entry={uid:crypto.randomUUID(),name:job.document_name,size:job.bytes||1,upload:100,process:job.progress||0,state:'uploaded',jobId:job.id,batchId:job.batch_id||job.id,serverName:job.document_name,autoIndex:true};this.entries.push(entry);
          if(!this.running){this.batch={id:entry.batchId,ids:[entry.uid],started:Date.parse(job.created_at)||Date.now(),finished:false};this.running=true;}else this.batch.ids.push(entry.uid);
        }
        if(entry)this.applyJob(entry,job);
      }
      const known=new Set(jobs.map((job)=>job.id));
      for(const entry of this.entries.filter((entry)=>entry.jobId&&activeStates.has(entry.state)&&!known.has(entry.jobId))){try{this.applyJob(entry,await this.api(`/api/jobs/${entry.jobId}`));}catch(error){if(error.message.includes('no está disponible'))this.fail(entry,{code:'NETWORK',detail:error.message});}}
      this.emit();this.finishCheck();
    }catch(error){if(this.entries.some((entry)=>entry.jobId&&activeStates.has(entry.state)))toast('Se perdió la conexión con el servidor local.');}
    finally{this.polling=false;if(this.entries.some((entry)=>entry.jobId&&activeStates.has(entry.state))||this.uploads)this.ensurePoll();}
  }
  currentEntries(){const fresh=this.entries.filter((entry)=>!entry.batchId);if(!this.running&&fresh.length)return fresh;return this.batch?this.entries.filter((entry)=>this.batch.ids.includes(entry.uid)):this.entries;}
  value(entry){if(['indexed','unindexed'].includes(entry.state))return 100;if(['uploaded','fragmenting','indexing'].includes(entry.state))return 40+.6*(entry.process||0);return .4*(entry.upload||0);}
  result(){
    const entries=this.currentEntries();
    return {kind:'batch',successes:entries.filter((entry)=>['indexed','unindexed'].includes(entry.state)).map((entry)=>({name:entry.name,chunks:entry.chunks||0})),failures:entries.filter((entry)=>['error','rejected','interrupted'].includes(entry.state)).map((entry)=>({name:entry.name,error:entry.error,retry:()=>this.retry(entry),remove:()=>this.remove(entry)})),unindexed:entries.some((entry)=>entry.state==='unindexed'),duration:(Date.now()-(this.batch?.started||Date.now()))/1000,retryAll:()=>entries.filter((entry)=>entry.error?.retryable).forEach((entry)=>this.retry(entry))};
  }
  finishCheck(){
    if(!this.running)return;const entries=this.currentEntries();
    if(entries.length&&entries.every((entry)=>terminal.has(entry.state))){this.running=false;this.batch.finished=true;this.emit();if(!this.notified.has(this.batch.id)){this.notified.add(this.batch.id);const result=this.result();this.save();if(result.successes.length||result.failures.length)this.completed(result);else toast('Cola cancelada');}}
  }
  save(){const entries=this.entries.map(({file,xhr,samples,...entry})=>entry);sessionStorage.setItem(STORE,JSON.stringify({entries,ignored:[...this.ignored],notified:[...this.notified],batch:this.batch,maxTotal:this.maxTotal}));}
  emit(){this.save();if(!this.frame)this.frame=requestAnimationFrame(()=>{this.frame=null;this.render();this.save();this.changed?.();});}
  render(){
    $('queue-section').hidden=!this.entries.length;
    const completed=this.entries.filter((entry)=>['indexed','unindexed'].includes(entry.state)).length;const errors=this.entries.filter((entry)=>['error','rejected','interrupted'].includes(entry.state)).length;
    $('queue-caption').textContent=`${this.entries.length} archivos · ${completed} completados · ${errors} con error`;
    const current=this.currentEntries();const valid=current.filter((entry)=>!['rejected','cancelled'].includes(entry.state));const total=valid.reduce((sum,entry)=>sum+Math.max(1,entry.size),0);
    const percent=total?valid.reduce((sum,entry)=>sum+this.value(entry)*Math.max(1,entry.size),0)/total:0;this.maxTotal=Math.max(this.maxTotal,percent);
    $('total-percent').textContent=`${number(this.maxTotal)} %`;$('total-progress').setAttribute('aria-valuenow',String(Math.round(this.maxTotal)));
    const widths=[0,0,0];for(const entry of valid){const group=['indexed','unindexed'].includes(entry.state)?0:entry.state==='uploading'?2:1;widths[group]+=this.value(entry)*Math.max(1,entry.size)/(total||1);}const sum=widths.reduce((a,b)=>a+b,0);if(sum>=this.maxTotal-.01)this.lastSegments=widths;segments($('total-progress'),this.lastSegments||widths);
    const now=performance.now();this.samples.push({time:now,value:this.maxTotal});this.samples=this.samples.filter((sample)=>sample.time>=now-3000);
    const first=this.samples[0];const span=first?(now-first.time)/1000:0;const rate=span?(this.maxTotal-first.value)/span:0;
    $('total-eta').textContent=this.maxTotal>=100?'Completado':span>=2&&rate>0?`Quedan ≈ ${Math.ceil((100-this.maxTotal)/rate)} s`:'Calculando…';
    const focused=document.activeElement?.closest('[data-queue-action]');const focusKey=focused?.dataset.queueAction;
    $('upload-queue').replaceChildren();
    for(const entry of this.entries){
      const row=node('li','queue-row');row.dataset.state=entry.state;const type=fileType(entry.name);type.classList.add('queue-file-icon');row.append(type);
      const body=node('div','queue-body');const line=node('div','queue-line');line.append(node('span','queue-name',entry.name),node('span',`status-pill ${phaseClass(entry.state)}`,entry.cancelRequested?'Cancelando…':labels[entry.state]));body.append(line);
      if(!['error','rejected','cancelled','interrupted'].includes(entry.state)){
        if(['fragmenting','indexing','uploaded'].includes(entry.state)){const phases=node('div','file-phases queue-progress');phases.append(progress(100,'moss',`${entry.name}: subida`),progress(entry.state==='indexing'?100:entry.stageProgress||0,'ochre',`${entry.name}: fragmentos`),progress(entry.state==='indexing'?entry.stageProgress||0:0,'ochre',`${entry.name}: índice`));body.append(phases);}
        else{const bar=progress(['indexed','unindexed'].includes(entry.state)?100:entry.upload||0,entry.state==='uploading'?'clay':'',entry.name);bar.classList.add('queue-progress');body.append(bar);}
      }
      let meta=`${size(entry.size)} · esperando turno (${this.entries.filter((item)=>item.state==='queued').indexOf(entry)+1}.º)`;
      if(entry.state==='uploading'){
        const samples=entry.samples||[];const first=samples[0];const seconds=first?(performance.now()-first.time)/1000:0;const speed=seconds>0?((entry.loaded||0)-first.bytes)/seconds:0;
        meta=`${size(entry.loaded||0)} de ${size(entry.size)}${speed>0?` · ${size(speed)}/s`:''}${seconds>=2&&speed>0?` · ≈ ${Math.ceil((entry.size-(entry.loaded||0))/speed)} s`:' · Calculando…'}`;
      }else if(entry.state==='indexed')meta=`${size(entry.size)} · ${number(entry.chunks||0)} fragmentos`;
      else if(entry.state==='unindexed')meta=`${size(entry.size)} · subido sin indexar`;
      else if(['fragmenting','indexing'].includes(entry.state))meta=`${size(entry.size)} · Paso ${entry.state==='fragmenting'?2:3} de 3: subida ✓ · fragmentos · índice`;
      else if(entry.state==='uploaded')meta=`${size(entry.size)} · subida ✓ · esperando procesado`;
      else if(['error','rejected','interrupted','cancelled'].includes(entry.state))meta=size(entry.size);
      const foot=node('div','queue-meta');foot.append(node('span','',meta),node('span','mono',`${number(this.value(entry))} %`));body.append(foot);
      if(entry.error)body.append(node('p','queue-error-text',entry.error.message));row.append(body);
      const actions=node('div','queue-row-actions');const add=(control,key)=>{control.dataset.queueAction=`${entry.uid}:${key}`;actions.append(control);};
      if(['queued','uploading','uploaded','fragmenting'].includes(entry.state)){const control=iconButton('x',`Cancelar ${entry.name}`,()=>this.cancel(entry));control.disabled=entry.cancellable===false||entry.cancelRequested;add(control,'cancel');}
      if(entry.state==='unindexed')add(button('Indexar ahora','button secondary',()=>this.retry(entry)), 'index');
      if(['error','interrupted'].includes(entry.state)&&entry.error?.retryable)add(button(entry.file||entry.serverName?'Reintentar':'Volver a añadir','button error-outline',()=>this.retry(entry)), 'retry');
      if(terminal.has(entry.state))add(iconButton('x',`Quitar ${entry.name}`,()=>this.remove(entry)), 'remove');
      row.append(actions);$('upload-queue').append(row);
    }
    if(focusKey&&(!document.activeElement||document.activeElement===document.body)){const target=[...$('upload-queue').querySelectorAll('[data-queue-action]')].find((element)=>element.dataset.queueAction===focusKey);(target||$('select-files')).focus({preventScroll:true});}
    const pending=this.entries.filter((entry)=>entry.state==='queued');const fresh=this.entries.filter((entry)=>!entry.batchId&&entry.state!=='cancelled');
    $('upload-button').disabled=!this.running&&!fresh.length;$('upload-button').setAttribute('aria-busy',String(this.running));
    $('upload-button').querySelector('span').textContent=this.running?`${this.paused?'En pausa':'Procesando'} ${current.filter((entry)=>terminal.has(entry.state)).length} de ${current.length}…`:`${$('auto-index').checked?'Subir e indexar':'Subir'} (${fresh.length})`;
    $('pause-queue').textContent=this.paused?'Reanudar':'Pausar';$('pause-queue').disabled=!this.running;
    $('cancel-pending').disabled=!this.entries.some((entry)=>['queued','uploading'].includes(entry.state));$('clear-queue').disabled=!this.entries.some((entry)=>['indexed','cancelled'].includes(entry.state));
    const count=this.entries.filter((entry)=>activeStates.has(entry.state)).length;$('nav-badge').classList.toggle('queued',count>0);if(count)$('nav-badge').textContent=`${count} en cola`;
  }
}
