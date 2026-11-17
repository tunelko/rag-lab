// Screen logic of the Graph view: sources, extraction options, batch progress, assessment figures and the
// register of relations. The canvas and the evidence panel are in graph-map.js; the server decides everything else.
import {$,node,toast,number} from './ui.js';
import {t,serverText,intlLocale} from './i18n.js';
import {collectionUrl,sessionKey} from './collection-scope.js';
import {GraphMap} from './graph-map.js';
const label=(key,values={})=>t(`graph.${key}`,values),STORE=sessionKey('musa-graph-job');
const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
// A continuous run can be paused and resumed; when its budget is spent it needs a new run, with new limits.
const RUNNING=['queued','running'],RESUMABLE=['paused','paused_error','paused_resources','interrupted'],clock=seconds=>{const total=Math.round(seconds||0);return Math.floor(total/60)+':'+String(total%60).padStart(2,'0');};
// The server reports finer stages than the three the screen shows: publishing and reconciling belong to the last one.
const STAGE={preparation:0,extraction:1,assessment:2,publication:2,reconciliation:2};
export class Graph{
  constructor(api){
    this.api=api;this.busy=false;this.active=false;this.audit_=null;this.mode='batch';this.task=null;
    this.map=new GraphMap();
    $('graph-select-all').addEventListener('click',()=>{for(const box of this.boxes())box.checked=true;this.sources();});
    $('graph-files').addEventListener('change',()=>this.sources());
    $('graph-prepare').addEventListener('click',()=>this.start('prepare'));
    $('graph-extract').addEventListener('click',()=>this.start(this.mode==='continuous'?'continuous':'process'));
    $('graph-audit').addEventListener('click',()=>this.audit());
    $('graph-reconcile').addEventListener('click',()=>this.start('reconcile'));
    $('graph-evaluate').addEventListener('click',()=>this.evaluate());
    $('graph-mode').addEventListener('click',event=>{const control=event.target.closest('[data-graph-mode]');if(control)this.setMode(control.dataset.graphMode);});
    $('graph-continuous-pause').addEventListener('click',()=>this.continuous('pause'));
    $('graph-continuous-resume').addEventListener('click',()=>this.continuous('resume'));
    $('graph-cancel').addEventListener('click',async()=>{if(!this.job)return;try{await this.api('/api/jobs/'+this.job,{method:'DELETE'});}catch(error){toast(error.message);}});
    $('graph-provider').addEventListener('change',()=>{$('graph-remote-field').hidden=$('graph-provider').value==='ollama';$('graph-remote').checked=false;this.loadModels();});
    $('graph-model').addEventListener('change',()=>this.controls());
    $('graph-model-refresh').addEventListener('click',()=>this.loadModels());
    const limit=$('graph-unit-limit');
    limit.addEventListener('input',()=>this.batchHint());
    for(const control of document.querySelectorAll('[data-graph-step]'))control.addEventListener('click',()=>{limit.value=String(Math.max(Number(limit.min),Math.min(Number(limit.max),(Number(limit.value)||0)+Number(control.dataset.graphStep))));this.batchHint();});
    $('graph-verification-close').addEventListener('click',()=>$('graph-verification-dialog').close());
    $('graph-verification-dialog').addEventListener('close',()=>this.verifyTrigger?.focus());
    $('graph-verification-form').addEventListener('submit',async event=>{event.preventDefault();try{await this.api('/api/graph/assertions/'+this.verifyRow.id+'/verification',post({status:$('graph-verification-status').value,note:$('graph-verification-note').value}));$('graph-verification-dialog').close();await this.refresh();}catch(error){toast(error.message);}});
    window.addEventListener('rag:languagechange',()=>{if(this.snapshot)this.render(this.snapshot);this.sources();this.renderAudit();this.setMode(this.mode);this.identity();if(this.task)this.renderTask(this.task);});
    this.stages(null);this.controls();
  }
  // One batch at a time, or a continuous run with explicit limits.
  setMode(mode){this.mode=mode;for(const control of $('graph-mode').querySelectorAll('button')){const on=control.dataset.graphMode===mode;control.setAttribute('aria-checked',String(on));control.tabIndex=on?0:-1;}$('graph-limits').hidden=mode!=='continuous';$('graph-extract').querySelector('span').textContent=label(mode==='continuous'?'process_continuous':'process');}
  boxes(){return [...$('graph-files').querySelectorAll('input')];}
  chosen(){return this.boxes().filter(box=>box.checked).map(box=>box.value);}
  // The list of sources: one checkbox per document that can be modelled.
  sources(){
    const boxes=this.boxes(),chosen=boxes.filter(box=>box.checked).length;
    for(const box of boxes)box.closest('li').classList.toggle('is-chosen',box.checked);
    $('graph-files-count').textContent=boxes.length?label('selected_count',{selected:number(chosen),total:number(boxes.length)}):'';
    $('graph-files-empty').hidden=boxes.length>0;
  }
  setStatus(status){
    const chosen=new Set(this.chosen());
    const names=status.documents.filter(document=>/\.(pdf|md|txt|markdown)$/i.test(document.name)).map(document=>document.name);
    const signature=JSON.stringify(names);if(this.signature===signature)return;this.signature=signature;
    $('graph-files').replaceChildren(...names.map(name=>{const item=node('li'),field=node('label'),box=node('input');box.type='checkbox';box.value=name;box.checked=chosen.has(name);field.append(box,node('span','',name));field.title=name;item.append(field);return item;}));
    this.sources();
  }
  async setActive(active){this.active=active;if(!active)return;try{await this.refresh();const job=sessionStorage.getItem(STORE);if(job&&!this.busy)this.follow(job);}catch(error){toast(error.message);}}
  async refresh(){
    this.snapshot=await this.api('/api/graph');this.render(this.snapshot);const result=await this.api('/api/graph/audit');this.audit_=result.audit||null;this.renderAudit();
    // Newer servers also offer continuous runs, reconciliation and measurement: the screen shows them only then.
    const modern=Array.isArray(this.snapshot.continuations);for(const part of document.querySelectorAll('[data-graph-modern]'))part.hidden=!modern;if(!modern)return;
    try{this.connections=await this.api('/api/graph/connections');}catch{this.connections=null;}
    this.identity();this.map.bridges(this.connections);
    const latest=this.snapshot.continuations[0];if(latest&&(RUNNING.includes(latest.status)||!this.task))this.watch(latest.id);
  }
  // Mentions joined across passages: only decided by the model and backed by its assessment.
  identity(){
    const data=this.connections,box=$('graph-identity');box.replaceChildren();if(!data)return;
    box.append(node('span','pill num',label('identity_entities',{count:number(data.canonical_entities)})),node('span','pill pill--mint num',label('identity_links',{count:number(data.identity_links.length)})),node('span','pill num',label('identity_conflicts',{count:number(data.identity_conflicts)})));
  }
  // The state of the continuous run: coverage of the chosen sources, what this run has spent, and what can be done next.
  renderTask(task){
    this.task=task;$('graph-continuous').hidden=!task;if(!task){this.controls();return;}
    const state=$('graph-continuous-state'),running=RUNNING.includes(task.status),total=task.units_total||0,done=task.units_done||0;
    state.textContent=label('task_'+task.status);state.className='pill pill--sm'+(running?' pill--lima':task.status==='done'?' pill--mint':task.status==='paused_budget'?' pill--butter':'');
    const [filled,rest]=$('graph-continuous-bar').children;filled.style.flexGrow=String(done);rest.style.flexGrow=String(Math.max(0,total-done));filled.hidden=!done;rest.hidden=total<=done;
    $('graph-continuous-bar').setAttribute('aria-label',label('task_coverage',{done:number(done),total:number(total),percent:number(task.coverage_percent||0,1)}));
    $('graph-continuous-figures').textContent=label('task_coverage',{done:number(done),total:number(total),percent:number(task.coverage_percent||0,1)})+' · '+label('task_spent',{processed:number(task.processed||0),limit:number(task.config.max_total_units),elapsed:clock(task.elapsed),minutes:number(task.config.max_seconds/60)});
    const reason=task.status==='paused_budget'?label('task_budget_note'):serverText(task.error||'');$('graph-continuous-error').textContent=reason;$('graph-continuous-error').hidden=!reason;
    $('graph-continuous-pause').hidden=!running;$('graph-continuous-resume').hidden=!RESUMABLE.includes(task.status);this.controls();
  }
  async watch(id){
    if(this.watching===id)return;this.watching=id;let seen=-1;
    try{while(this.watching===id){const task=await this.api('/api/graph/continuous/'+id);this.renderTask(task);
      // The map follows the run: redraw when another passage is finished.
      if(seen>=0&&task.units_done!==seen){this.snapshot=await this.api('/api/graph');this.render(this.snapshot);}seen=task.units_done;
      if(!RUNNING.includes(task.status)||!this.active)break;await new Promise(resolve=>setTimeout(resolve,2000));}
    }catch(error){$('graph-continuous-error').textContent=error.message;$('graph-continuous-error').hidden=false;}finally{if(this.watching===id)this.watching=null;}
  }
  async continuous(action){
    if(!this.task)return;
    try{await this.api('/api/graph/continuous/'+this.task.id+'/'+action,{method:'POST'});this.watching=null;await this.watch(this.task.id);}catch(error){toast(error.message);}
  }
  // Retrieval with and without the graph, on the same index; the report says what it does not measure.
  async evaluate(){
    if(this.busy)return;const content=$('graph-eval-content').value;$('graph-eval-error').hidden=true;
    if(!content.trim()){$('graph-eval-content').focus();return;}
    this.busy=true;this.action='evaluate';this.controls();
    try{const queued=await this.api('/api/graph/evaluate',post({content,k:Number($('graph-eval-k').value),rerank_device:$('graph-eval-device').value}));sessionStorage.setItem(STORE,queued.job_id);const job=await this.follow(queued.job_id);if(job?.evaluation)this.report(job.evaluation);}
    catch(error){$('graph-eval-error').textContent=error.message;$('graph-eval-error').hidden=false;this.busy=false;this.controls();}
  }
  report(data){
    const base=data.summaries.baseline,graph=data.summaries.graph,rows=Object.keys(base).filter(key=>typeof base[key]==='number'&&typeof graph[key]==='number');
    $('graph-eval-rows').replaceChildren(...rows.map(key=>{const row=node('tr'),delta=data.delta?.[key]??graph[key]-base[key];row.append(node('td','',key.replaceAll('_',' ')),node('td','num',number(base[key],3)),node('td','num',number(graph[key],3)),node('td','num'+(delta>0?' is-up':delta<0?' is-down':''),(delta>0?'+':'')+number(delta,3)));return row;}));
    const parameters=data.parameters||{};$('graph-eval-note').textContent=[label('measure_scope'),parameters.reranker?label(parameters.reranker_loaded?'measure_reranker':'measure_no_reranker',{device:parameters.reranker_actual_device||'—'}):'',label('measure_counts',{assertions:number(data.graph_assertions||0),links:number(data.identity_decisions||0)})].filter(Boolean).join(' ');
    $('graph-eval-report').hidden=false;
  }
  // Documentary verification: files and quotes checked, and when.
  renderAudit(){
    const data=this.audit_,pills=$('graph-audit-summary');pills.replaceChildren();
    if(!data){$('graph-audit-date').textContent='';this.assessment(this.snapshot?.assessment_counts);return;}
    pills.append(node('span','pill pill--mint num',label('audit_checked',{checked:number(data.checked_documents),total:number(data.total_documents)})),node('span','pill num',label('audit_issues',{count:number(data.issues.length)})),node('span','pill num',label('audit_duplicates',{count:number(data.duplicate_groups.length)})));
    $('graph-audit-date').textContent=label('audit_last',{date:new Date(data.completed).toLocaleString(intlLocale(),{dateStyle:'medium',timeStyle:'short'})});
    $('graph-audit-report').href=collectionUrl('/api/graph/audit/report');$('graph-audit-report').hidden=false;
    // Whole-register figures when the server gives them; otherwise the ones of the last verification.
    const counts=this.snapshot?.assessment_counts,audit=data.source_assessments;
    this.assessment(counts||(audit?{accepted:audit.accepted,rejected:audit.rejected,unverified:audit.pending}:null));
  }
  // Accepted, discarded and not yet assessed, as a proportion bar with its figures.
  assessment(counts){
    $('graph-assessment').hidden=!counts;if(!counts)return;
    const parts=[['accepted',counts.accepted||0,'pill--mint'],['rejected',counts.rejected||0,'pill--coral'],['unverified',counts.unverified||0,'']],total=parts.reduce((sum,[,value])=>sum+value,0);
    $('graph-ratio').replaceChildren(...parts.filter(([,value])=>value).map(([key,value])=>{const part=node('i','ratio__'+key);part.style.flexGrow=String(value);return part;}));
    $('graph-ratio').setAttribute('aria-label',parts.map(([key,value])=>label(key+'_count',{count:number(value)})).join(' · '));
    $('graph-ratio').classList.toggle('is-empty',!total);
    $('graph-assessment-figures').replaceChildren(...parts.map(([key,value,tone])=>node('span','pill num '+tone,label(key+'_count',{count:number(value)}))));
  }
  async audit(){if(this.busy)return;this.busy=true;this.controls();try{const queued=await this.api('/api/graph/audit',post({all_collections:true}));sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);this.busy=false;this.controls();}}
  async loadModels(){
    const provider=$('graph-provider').value,previous=$('graph-model').value,sequence=(this.modelSequence||0)+1;this.modelSequence=sequence;
    try{const data=await this.api(provider==='ollama'?'/api/settings':'/api/providers/'+provider+'/models');if(sequence!==this.modelSequence)return;
      const names=data.models.map(row=>typeof row==='string'?row:row.name);$('graph-model').replaceChildren(node('option','',label('choose_model')),...names.map(name=>{const option=node('option','',name);option.value=name;return option;}));$('graph-model').firstElementChild.value='';
      if(names.includes(previous))$('graph-model').value=previous;else if(names.includes(this.snapshot?.extraction_settings?.model))$('graph-model').value=this.snapshot.extraction_settings.model;
    }catch(error){toast(error.message);}
    this.controls();
  }
  render(data){
    if(!this.modelInitialized){this.modelInitialized=true;if(data.extraction_settings?.provider)$('graph-provider').value=data.extraction_settings.provider;$('graph-remote-field').hidden=$('graph-provider').value==='ollama';this.loadModels();}
    $('graph-summary').textContent=label('summary',{documents:number(data.documents),units:number(data.units),modelled:number(data.modelled_units),assertions:number(data.assertions)});
    $('graph-pill-documents').textContent=label('documents_count',{count:number(data.documents)});
    $('graph-pill-units').textContent=label('units_count',{modelled:number(data.modelled_units),units:number(data.units)});
    $('graph-pill-assertions').textContent=label('assertions_count',{count:number(data.assertions)});
    this.map.render(data.rows);this.batchHint();
    $('graph-results').replaceChildren(...data.rows.map(row=>{
      const article=node('article');article.append(node('strong','',`${row.subject_label} → ${row.predicate} → ${row.object_label}`),node('p','meta',`${label(row.basis)} · ${label(row.polarity)} · ${label(row.review)} · ${label('verification_'+row.verification)}`),node('blockquote','evidence-quote',row.quote));if(row.verification_note)article.append(node('p','meta',row.verification_note));
      const actions=node('div','graph-row-actions'),source=node('button','btn btn--secondary btn--sm',row.file+(row.page?' · '+row.page:''));source.type='button';
      source.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('rag:document',{detail:{url:collectionUrl('/api/graph/source?unit='+row.unit),file:row.file,page:row.page||1,trigger:source}})));
      const verify=node('button','btn btn--secondary btn--sm',label('verify'));verify.type='button';verify.addEventListener('click',()=>this.verify(row,verify));actions.append(source,verify);
      for(const state of ['accepted','rejected','proposed']){const button=node('button','btn btn--secondary btn--sm',label('review_'+state));button.type='button';button.disabled=row.review===state||(state==='accepted'&&row.verification!=='supported');button.addEventListener('click',async()=>{try{await this.api('/api/graph/assertions/'+row.id+'/review',post({state}));await this.refresh();}catch(error){toast(error.message);}});actions.append(button);}
      article.append(actions);return article;
    }));
    if(!data.rows.length)$('graph-results').append(node('p','meta',label('empty')));
    if(data.truncated)$('graph-results').append(node('p','meta',label('truncated',{limit:data.limit})));
    $('graph-runs').replaceChildren(...data.runs.map(run=>{const row=node('div');row.append(node('span','meta',run.id.slice(0,10)+' · '+run.status));if(run.error)row.append(node('span','',run.error));
      if(['error','interrupted','cancelled'].includes(run.status)){const button=node('button','btn btn--secondary btn--sm',label('resume'));button.type='button';button.disabled=this.busy;button.addEventListener('click',()=>this.resume(run.id));row.append(button);}return row;}));
    this.renderAudit();
  }
  async start(action){
    if(this.busy)return;
    let files=this.chosen();if(!files.length&&action==='process')files=this.boxes().map(box=>box.value);if(!files.length){toast(label('select_files'));return;}
    if(files.length>100){$('graph-options').scrollIntoView({block:'nearest'});toast(label('max_files'));return;}
    const body={files};if(['extract','process','continuous','reconcile'].includes(action)){
      if(!$('graph-unit-limit').reportValidity())return;
      Object.assign(body,{provider:$('graph-provider').value,model:$('graph-model').value.trim(),max_units:Number($('graph-unit-limit').value),remote_content:$('graph-remote').checked});
      if(!body.model){$('graph-model').focus();toast(label('select_model'));return;}
      if(body.provider!=='ollama'&&!body.remote_content){$('graph-remote').focus();toast(label('remote'));return;}
      if(action==='continuous'){for(const id of ['graph-max-units','graph-max-minutes','graph-max-memory'])if(!$(id).reportValidity())return;
        Object.assign(body,{max_units:1,max_total_units:Number($('graph-max-units').value),max_seconds:60*Number($('graph-max-minutes').value),max_rss_mb:1024*Number($('graph-max-memory').value)});}
    }
    // A continuous run is not one job: the server queues a passage at a time, and the screen watches the run.
    if(action==='continuous'){try{const started=await this.api('/api/graph/continuous',post(body));this.watching=null;this.active=true;await this.watch(started.task_id);}catch(error){toast(error.message);}return;}
    this.busy=true;this.action=action;this.controls();
    try{const queued=await this.api('/api/graph/'+action,post(body));sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);this.busy=false;this.controls();}
  }
  batchHint(){$('graph-batch-hint').textContent=label('batch_hint',{limit:$('graph-unit-limit').value});}
  async verify(row,trigger){try{const result=await this.api('/api/graph/evidence/'+row.unit);this.verifyRow=row;this.verifyTrigger=trigger;$('graph-verification-claim').textContent=`${row.subject_label} → ${row.predicate} → ${row.object_label}`;$('graph-verification-evidence').textContent=result.text;$('graph-verification-note').value=row.verification_note||'';$('graph-verification-status').value=row.verification==='pending'?'':row.verification;$('graph-verification-dialog').showModal();}catch(error){toast(error.message);}}
  async resume(run){if(this.busy)return;try{const queued=await this.api('/api/graph/runs/'+run+'/resume',{method:'POST'});sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);}}
  // Without a model there is nothing to extract with: the field says so and the main action waits.
  controls(){
    const missing=!$('graph-model').value,running=RUNNING.includes(this.task?.status),held=this.busy||running;
    for(const id of ['graph-prepare','graph-select-all','graph-audit','graph-evaluate'])$(id).disabled=held;
    $('graph-extract').disabled=held||missing;$('graph-reconcile').disabled=held||missing;$('graph-cancel').hidden=!this.busy;
    $('graph-model').classList.toggle('is-missing',missing);$('graph-model-reason').hidden=!missing;
  }
  // The three stages of a batch: the one working in lime, the finished ones in mint.
  stages(current){[...$('graph-stages').children].forEach((item,index)=>{const now=current===index;item.classList.toggle('is-current',now);item.classList.toggle('is-done',current!=null&&index<current);if(now)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});}
  async follow(id){
    this.job=id;this.busy=true;this.controls();$('graph-progress').hidden=false;
    try{while(true){const job=await this.api('/api/jobs/'+id),ended=['done','error','cancelled'].includes(job.status);$('graph-progress').value=ended?job.progress||0:Math.min(job.progress||0,99);$('graph-message').textContent=serverText(job.phase);$('graph-message').title='';window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));
      // The stage comes from the server when it reports one; preparing evidence is always the first.
      this.stages(job.graph_stage in STAGE?STAGE[job.graph_stage]:this.action==='prepare'?0:null);
      if(['done','error','cancelled'].includes(job.status)){sessionStorage.removeItem(STORE);if(job.error_info||job.error){$('graph-message').textContent=serverText(job.error_info?.message||job.error);$('graph-message').title=job.error_info?.detail||'';}await this.refresh();return job;}await new Promise(resolve=>setTimeout(resolve,1500));}
    }catch(error){$('graph-message').textContent=error.message;if(error.status===404){sessionStorage.removeItem(STORE);await this.refresh();}}finally{this.job=null;this.busy=false;this.action=null;this.stages(null);$('graph-progress').hidden=true;this.controls();}
  }
}
