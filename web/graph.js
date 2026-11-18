// Screen logic of the Graph view: sources, extraction options, batch progress, assessment figures and the
// register of relations. The canvas and the evidence panel are in graph-map.js; the server decides everything else.
import {$,node,toast,number} from './ui.js';
import {t,serverText,intlLocale} from './i18n.js';
import {collectionUrl,sessionKey} from './collection-scope.js';
import {GraphMap} from './graph-map.js';
const label=(key,values={})=>t(`graph.${key}`,values),STORE=sessionKey('musa-graph-job');
const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
// The server takes at most this many sources in one request; newer servers report their own limit.
const MAX_FILES=100,PAGE=100,CHECKS=['preparation','extraction','assessment','publication'],SVG='http://www.w3.org/2000/svg';
// The state of a document in the list, and the colour of its label.
const TONE={complete:' pill--mint',in_use:' pill--lima',partial:' pill--butter',stale:' pill--coral',prepared:''};
// A continuous run can be paused and resumed; when its budget is spent it needs a new run, with new limits.
const RUNNING=['queued','running'],RESUMABLE=['paused','paused_error','paused_resources','interrupted'],clock=seconds=>{const total=Math.round(seconds||0);return Math.floor(total/60)+':'+String(total%60).padStart(2,'0');};
// The stage a job reports, as its position in the row of stages.
const STAGE={preparation:0,extraction:1,assessment:2,publication:3,reconciliation:4};
export class Graph{
  constructor(api){
    this.api=api;this.busy=false;this.active=false;this.audit_=null;this.task=null;this.max=MAX_FILES;this.modern=false;this.selected=new Set();this.offset=0;this.documents=null;
    this.map=new GraphMap();
    // Choose every source (when the collection fits in one request), the next ones still pending, or none.
    $('graph-select-all').addEventListener('click',()=>{
      if(this.modern){for(const row of this.documents?.documents||[])if(row.eligible)this.selected.add(row.file);this.renderDocuments();}
      else{for(const box of this.boxes())box.checked=true;this.sources();}
    });
    $('graph-select-max').addEventListener('click',()=>this.selectMax());
    $('graph-select-none').addEventListener('click',()=>{if(this.modern){this.selected.clear();this.renderDocuments();}else{for(const box of this.boxes())box.checked=false;this.sources();}});
    $('graph-files').addEventListener('change',event=>{
      const box=event.target,full=box.checked&&(this.modern?this.selected.size>=this.max:this.chosen().length>this.max);
      if(full){box.checked=false;toast(label('select_limit',{max:number(this.max)}));}
      else if(this.modern){
        if(box.checked)this.selected.add(box.value);
        // A processed or busy document that is taken out cannot be put back.
        else{this.selected.delete(box.value);box.disabled=box.closest('li').classList.contains('is-blocked');}
      }
      this.sources();
    });
    $('graph-page-prev').addEventListener('click',()=>this.loadDocuments(Math.max(0,this.offset-PAGE)));
    $('graph-page-next').addEventListener('click',()=>this.loadDocuments(this.offset+PAGE));
    $('graph-prepare').addEventListener('click',()=>this.start('prepare'));
    // Processing is continuous wherever the server offers it; an older server processes the next batch.
    for(const id of ['graph-extract','graph-process'])$(id).addEventListener('click',()=>this.start(this.modern?'continuous':'process'));
    $('graph-continuous-again').addEventListener('click',()=>this.again());
    $('graph-audit').addEventListener('click',()=>this.audit());
    $('graph-reconcile').addEventListener('click',()=>this.join());
    $('graph-evaluate').addEventListener('click',()=>this.evaluate());
    $('graph-continuous-pause').addEventListener('click',()=>this.continuous('pause'));
    $('graph-continuous-resume').addEventListener('click',()=>this.continuous('resume'));
    $('graph-cancel').addEventListener('click',async()=>{if(!this.job)return;try{await this.api('/api/jobs/'+this.job,{method:'DELETE'});}catch(error){toast(error.message);}});
    $('graph-provider').addEventListener('change',()=>{$('graph-remote-field').hidden=$('graph-provider').value==='ollama';$('graph-remote').checked=false;this.loadModels();});
    $('graph-model').addEventListener('change',()=>{this.controls();if(this.modern)this.loadDocuments();else this.check();});
    $('graph-model-refresh').addEventListener('click',()=>this.loadModels());
    const limit=$('graph-unit-limit');
    limit.addEventListener('input',()=>this.batchHint());
    for(const control of document.querySelectorAll('[data-graph-step]'))control.addEventListener('click',()=>{limit.value=String(Math.max(Number(limit.min),Math.min(Number(limit.max),(Number(limit.value)||0)+Number(control.dataset.graphStep))));this.batchHint();});
    $('graph-verification-close').addEventListener('click',()=>$('graph-verification-dialog').close());
    $('graph-verification-dialog').addEventListener('close',()=>this.verifyTrigger?.focus());
    $('graph-verification-form').addEventListener('submit',async event=>{event.preventDefault();try{await this.api('/api/graph/assertions/'+this.verifyRow.id+'/verification',post({status:$('graph-verification-status').value,note:$('graph-verification-note').value}));$('graph-verification-dialog').close();await this.refresh();}catch(error){toast(error.message);}});
    window.addEventListener('rag:languagechange',()=>{if(this.snapshot)this.render(this.snapshot);this.sources();this.renderAudit();this.identity();if(this.task)this.renderTask(this.task);if(this.checks)this.renderChecks(this.checks,this.checkScope);});
    this.stages(null);this.controls();
  }
  // The next sources still to process, as the server lists them; on an older server, the first ones of the list.
  async selectMax(){
    if(this.modern){
      try{const data=await this.api('/api/graph/documents?'+this.query({limit:'1',eligible_only:'true'}));this.max=data.max_batch_size||this.max;this.selected=new Set(data.next_batch);if(!data.next_batch.length)toast(label('nothing_pending'));await this.loadDocuments(0);}
      catch(error){toast(error.message);}
      return;
    }
    let count=0;for(const box of this.boxes()){box.checked=count<this.max;if(box.checked)count++;}
    this.sources();
  }
  query(extra){return new URLSearchParams({provider:$('graph-provider').value,model:$('graph-model').value.trim(),...extra});}
  // One page of the collection, with what the server knows of each document for the chosen model.
  async loadDocuments(offset=this.offset){
    if(!this.modern)return;
    const ticket=this.pageTicket=(this.pageTicket||0)+1;
    try{const data=await this.api('/api/graph/documents?'+this.query({offset:String(offset),limit:String(PAGE)}));if(ticket!==this.pageTicket)return;
      this.documents=data;this.offset=offset;this.max=data.max_batch_size||this.max;this.renderDocuments();
    }catch(error){if(ticket===this.pageTicket)toast(error.message);}
  }
  renderDocuments(){
    const data=this.documents;if(!data)return;
    $('graph-files').replaceChildren(...data.documents.map(row=>{
      const item=node('li'),field=node('label'),box=node('input'),chosen=this.selected.has(row.file);
      box.type='checkbox';box.value=row.file;box.checked=chosen;
      // A processed or busy document is dimmed and cannot be added; one already chosen can still be taken out.
      box.disabled=!row.eligible&&!chosen;item.classList.toggle('is-blocked',!row.eligible);
      field.append(box,node('span','',row.file));field.title=row.file;
      if(row.status in TONE)field.append(node('span','pill pill--sm'+TONE[row.status],label('doc_'+row.status)));
      item.append(field);return item;
    }));
    const last=this.offset+data.documents.length;
    $('graph-pager').hidden=data.total_documents<=PAGE;$('graph-page-prev').disabled=!this.offset;$('graph-page-next').disabled=last>=data.total_documents;
    $('graph-page-range').textContent=label('page_range',{first:number(data.documents.length?this.offset+1:0),last:number(last),total:number(data.total_documents)});
    this.sources();
  }
  boxes(){return [...$('graph-files').querySelectorAll('input')];}
  // On a newer server the list shows one page of the collection, so the selection is kept apart from it.
  chosen(){return this.modern?[...this.selected]:this.boxes().filter(box=>box.checked).map(box=>box.value);}
  total(){return this.modern?this.documents?.total_documents||0:$('graph-files').childElementCount;}
  sources(){
    const chosen=this.chosen().length,total=this.total(),limit=Math.min(this.max,total);
    for(const box of this.boxes())box.closest('li').classList.toggle('is-chosen',box.checked);
    $('graph-files-count').textContent=total?label('selected_count',{selected:number(chosen),total:number(total),max:number(limit)}):'';
    $('graph-select-max').textContent=label('select_max',{max:number(limit)});$('graph-select-none').hidden=!chosen;
    // A collection larger than one request cannot be chosen whole: the note says how to go through it.
    const over=total>this.max;$('graph-select-note').hidden=!over;if(over)$('graph-select-note').textContent=label('select_all_note',{total:number(total),max:number(this.max)});
    $('graph-files-empty').hidden=total>0||(this.modern&&!this.documents);this.controls();this.check();
  }
  // What the server has verified for the chosen sources, stage by stage. Asked again whenever the selection or the model changes.
  check(){clearTimeout(this.checkTimer);this.checkTimer=setTimeout(()=>this.runCheck(),350);}
  async runCheck(){
    const files=this.chosen(),ticket=this.checkTicket=(this.checkTicket||0)+1;
    // With nothing chosen the checks describe the whole collection, from the page of documents already read.
    if(this.modern&&!files.length&&this.documents?.selection_checks){this.renderChecks(this.documents,'collection');return;}
    if(!this.modern||!files.length||files.length>this.max){this.renderChecks(null);return;}
    try{const data=await this.api('/api/graph/documents/check',post({files,provider:$('graph-provider').value,model:$('graph-model').value.trim()}));if(ticket===this.checkTicket)this.renderChecks(data);}
    catch{if(ticket===this.checkTicket)this.renderChecks(null);}
  }
  renderChecks(data,scope='selection'){
    this.checks=data;this.checkScope=scope;$('graph-checks').hidden=!data;this.states=[];
    $('graph-checks-title').textContent=label(scope==='collection'?'checks_collection':'checks_title');
    if(!data){this.stages();return;}
    const rows=CHECKS.map(key=>({key,...data.selection_checks[key]}));
    rows.push({key:'connection',state:data.reconciliation_state||(data.reconciliation_checked?'verified':'pending')});
    // A step that does not apply is settled, but nothing was verified: it gets a dash, never a tick.
    this.states=rows.map(row=>row.state==='verified'?'done':row.state==='not_required'?'skip':row.state==='partial'?'partial':'idle');
    const settled=index=>['done','skip'].includes(this.states[index]);
    $('graph-checks-list').replaceChildren(...rows.map((row,index)=>{
      const state=this.states[index],item=node('li'),mark=node('span','check'),text=node('span');item.dataset.state=state;
      if(state==='done'){const icon=document.createElementNS(SVG,'svg'),use=document.createElementNS(SVG,'use');icon.setAttribute('class','icon');icon.setAttribute('aria-hidden','true');use.setAttribute('href','#i-check');icon.append(use);mark.append(icon);}
      let detail;
      if(row.key==='connection')detail=label(state==='done'?'check_connection_ok':state==='skip'?'check_connection_none':row.state==='model_required'?'check_model':settled(3)?'check_connection_left':'check_pending');
      else if(state==='skip')detail=label(row.key==='assessment'?'check_assessment_none':'check_not_required');
      else detail=row.done?label('check_count',{done:number(row.done),total:number(row.total)}):label('check_pending');
      text.append(node('strong','',label('check_'+row.key)),node('span','meta',' · '+detail));if(state==='done')text.append(node('span','sr-only',' '+label('check_ok')));
      item.append(mark,text);return item;
    }));
    const verified=this.states.filter(state=>state==='done').length,closed=rows.filter((row,index)=>settled(index)).length,pill=$('graph-checks-state');
    pill.textContent=verified===rows.length?label('checks_all'):closed===rows.length?label('checks_settled',{verified}):label('checks_some',{done:closed,total:rows.length});
    pill.className='pill pill--sm'+(verified===rows.length?' pill--mint':'');
    this.stages();
  }
  setStatus(status){
    const names=status.documents.filter(document=>/\.(pdf|md|txt|markdown)$/i.test(document.name)).map(document=>document.name);
    const signature=JSON.stringify(names);if(this.signature===signature)return;this.signature=signature;
    // A newer server lists the documents itself, a page at a time and with their state.
    if(this.modern){this.loadDocuments();return;}
    const chosen=new Set(this.chosen());
    $('graph-files').replaceChildren(...names.map(name=>{const item=node('li'),field=node('label'),box=node('input');box.type='checkbox';box.value=name;box.checked=chosen.has(name);field.append(box,node('span','',name));field.title=name;item.append(field);return item;}));
    this.sources();
  }
  async setActive(active){this.active=active;if(!active)return;try{await this.refresh();const job=sessionStorage.getItem(STORE);if(job&&!this.busy)this.follow(job);}catch(error){toast(error.message);}}
  async refresh(){
    this.snapshot=await this.api('/api/graph');this.render(this.snapshot);const result=await this.api('/api/graph/audit');this.audit_=result.audit||null;this.renderAudit();
    // Newer servers also offer continuous runs, reconciliation and measurement: the screen shows them only then.
    const modern=this.modern=Array.isArray(this.snapshot.continuations);for(const part of document.querySelectorAll('[data-graph-modern]'))part.hidden=!modern;
    // With continuous processing the size of one job is not the user's concern.
    $('graph-unit-field').hidden=modern;$('graph-batch-hint').hidden=modern;if(!modern){this.check();return;}
    // The state of each document changes with every job: the page is read again.
    this.loadDocuments();
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
  // The batch of pairs being joined now: three steps, the working one in lime. A batch is not the whole work: how many
  // pairs are already decided and how many are left is told apart, in its own line.
  paintJoin(box,batch){
    box.hidden=!batch;if(!batch)return;
    const done=batch.steps_done||0,saved=batch.phase==='published';
    [...box.querySelectorAll('li')].forEach((item,index)=>{const now=!saved&&index===done;item.classList.toggle('is-done',index<done||saved);item.classList.toggle('is-current',now);if(now)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});
    box.querySelector('p').textContent=saved?label('join_saved',{pairs:number(batch.batch_pairs||0)}):label('join_batch',{pairs:number(batch.batch_pairs||0),step:Math.min(done+1,batch.steps_total||3),total:batch.steps_total||3});
  }
  renderTask(task){
    this.task=task;$('graph-continuous').hidden=!task;if(!task){this.controls();return;}
    // Passages processed and pairs joined are different counts: full coverage of passages is not the end of the joining.
    const joining=task.reconciliation_progress,pairs=joining?joining.assessed_pairs+joining.pending_pairs:0;
    $('graph-continuous-join').hidden=!pairs;if(pairs)$('graph-continuous-join').textContent=label('join_counts',{assessed:number(joining.assessed_pairs),pending:number(joining.pending_pairs)});
    this.paintJoin($('graph-continuous-batch'),joining?.batch||null);
    const state=$('graph-continuous-state'),running=RUNNING.includes(task.status),total=task.units_total||0,done=task.units_done||0,left=Math.max(0,total-done);
    state.textContent=label('task_'+task.status);state.className='pill pill--sm'+(running?' pill--lima':task.status==='done'?' pill--mint':task.status==='paused_budget'?' pill--butter':'');
    const [filled,rest]=$('graph-continuous-bar').children;filled.style.flexGrow=String(done);rest.style.flexGrow=String(left);filled.hidden=!done;rest.hidden=!left;
    $('graph-continuous-bar').setAttribute('aria-label',label('task_coverage',{done:number(done),total:number(total),percent:number(task.coverage_percent||0,1)}));
    $('graph-continuous-figures').textContent=label('task_coverage',{done:number(done),total:number(total),percent:number(task.coverage_percent||0,1)})+' · '+label('task_spent',{processed:number(task.processed||0),limit:number(task.config.max_total_units),elapsed:clock(task.elapsed),minutes:number(task.config.max_seconds/60)});
    // A run that stopped at its limit says, in plain words, what is left; going on is a new run the user starts.
    const pending=left>0||Boolean(task.reconciliation_pending);
    const reason=task.status==='paused_budget'?(left?label('task_budget_note',{left:number(left)}):task.reconciliation_pending?label('task_reconcile_note'):label('task_budget_done')):serverText(task.error||'');
    $('graph-continuous-error').textContent=reason;$('graph-continuous-error').hidden=!reason;
    $('graph-continuous-pause').hidden=!running;$('graph-continuous-resume').hidden=!RESUMABLE.includes(task.status);
    // Sending passages to a remote provider is authorised in the form, so another round with one starts from «Procesar selección».
    $('graph-continuous-again').hidden=task.status!=='paused_budget'||!pending||task.config.provider!=='ollama';this.controls();
  }
  // Another round on the same sources, with the same limits: a new run, started by the user.
  async again(){
    const config=this.task?.config;if(!config||this.busy)return;
    try{const started=await this.api('/api/graph/continuous',post({files:config.files,provider:config.provider,model:config.model,remote_content:false,max_units:1,max_total_units:config.max_total_units,max_seconds:config.max_seconds,max_rss_mb:config.max_rss_mb}));this.watching=null;await this.watch(started.task_id);}
    catch(error){toast(error.message);}
  }
  async watch(id){
    if(this.watching===id)return;this.watching=id;let seen=-1;
    try{while(this.watching===id){const task=await this.api('/api/graph/continuous/'+id);this.renderTask(task);
      // The map follows the run: redraw when another passage is finished.
      if(seen>=0&&task.units_done!==seen){this.snapshot=await this.api('/api/graph');this.render(this.snapshot);this.loadDocuments();}seen=task.units_done;
      if(!RUNNING.includes(task.status)){if(seen>=0)this.loadDocuments();break;}if(!this.active)break;await new Promise(resolve=>setTimeout(resolve,2000));}
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
    this.controls();this.loadDocuments();
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
    $('graph-register-count').textContent=label('assertions_count',{count:number(data.rows.length)});
    if(!data.rows.length)$('graph-results').append(node('p','meta',label('empty')));
    if(data.truncated)$('graph-results').append(node('p','meta',label('truncated',{limit:data.limit})));
    $('graph-runs').replaceChildren(...data.runs.map(run=>{const row=node('div');row.append(node('span','meta',run.id.slice(0,10)+' · '+run.status));if(run.error)row.append(node('span','',run.error));
      if(['error','interrupted','cancelled'].includes(run.status)){const button=node('button','btn btn--secondary btn--sm',label('resume'));button.type='button';button.disabled=this.busy;button.addEventListener('click',()=>this.resume(run.id));row.append(button);}return row;}));
    this.renderAudit();
  }
  // Joining repeated entities works on the documents the server names, which may be processed ones that cannot be chosen in the list.
  async join(){
    if(this.busy)return;
    try{const data=await this.api('/api/graph/documents?'+this.query({limit:'1'}));if(!data.reconciliation_batch.length){toast(label('nothing_to_join'));return;}await this.start('reconcile',data.reconciliation_batch);}
    catch(error){toast(error.message);}
  }
  async start(action,given){
    if(this.busy)return;
    let files=given||this.chosen();if(!files.length&&action==='process')files=this.boxes().map(box=>box.value);if(!files.length){toast(label('select_files'));return;}
    if(files.length>this.max){$('graph-options').scrollIntoView({block:'nearest'});toast(label('max_files'));return;}
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
    this.busy=true;this.action=action;this.controls();this.paintJoin($('graph-join'),null);
    try{const queued=await this.api('/api/graph/'+action,post(body));sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);this.busy=false;this.controls();}
  }
  batchHint(){$('graph-batch-hint').textContent=label('batch_hint',{limit:$('graph-unit-limit').value});}
  async verify(row,trigger){try{const result=await this.api('/api/graph/evidence/'+row.unit);this.verifyRow=row;this.verifyTrigger=trigger;$('graph-verification-claim').textContent=`${row.subject_label} → ${row.predicate} → ${row.object_label}`;$('graph-verification-evidence').textContent=result.text;$('graph-verification-note').value=row.verification_note||'';$('graph-verification-status').value=row.verification==='pending'?'':row.verification;$('graph-verification-dialog').showModal();}catch(error){toast(error.message);}}
  async resume(run){if(this.busy)return;try{const queued=await this.api('/api/graph/runs/'+run+'/resume',{method:'POST'});sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);}}
  // Without a model there is nothing to extract with: the field says so and the main action waits.
  controls(){
    const missing=!$('graph-model').value,running=RUNNING.includes(this.task?.status),held=this.busy||running;
    for(const id of ['graph-prepare','graph-select-max','graph-select-none','graph-audit','graph-evaluate','graph-continuous-again'])$(id).disabled=held;
    $('graph-select-all').disabled=held||this.total()>this.max;
    for(const id of ['graph-extract','graph-process','graph-reconcile'])$(id).disabled=held||missing;
    $('graph-cancel').hidden=!this.busy;$('graph-model').classList.toggle('is-missing',missing);$('graph-model-reason').hidden=!missing;
  }
  // A verified stage shows the same drawn tick as the checklist; one that does not apply, a dash; the others their number.
  tick(badge,mark,number){
    if(badge.dataset.mark===mark)return;badge.dataset.mark=mark;
    if(mark==='done'){const icon=document.createElementNS(SVG,'svg'),use=document.createElementNS(SVG,'use');icon.setAttribute('class','icon');icon.setAttribute('aria-hidden','true');use.setAttribute('href','#i-check');icon.append(use);badge.replaceChildren(icon);}
    else if(mark==='skip')badge.replaceChildren(node('span','dash'));
    else badge.textContent=String(number);
  }
  // The stages: the one working in lime; in mint, with a tick, the ones the server has verified for the selection.
  stages(current=this.stage){
    this.stage=current;
    [...$('graph-stages').children].forEach((item,index)=>{
      const now=current===index,done=!now&&(this.states?.[index]==='done'||(current!=null&&index<current)),skip=!now&&!done&&this.states?.[index]==='skip';
      item.classList.toggle('is-current',now);item.classList.toggle('is-done',done);item.classList.toggle('is-skipped',skip);this.tick(item.querySelector('.step-no'),done?'done':skip?'skip':'',index+1);
      if(now)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');
    });
  }
  async follow(id){
    this.job=id;this.busy=true;this.controls();$('graph-progress').hidden=false;
    try{while(true){const job=await this.api('/api/jobs/'+id),ended=['done','error','cancelled'].includes(job.status);$('graph-progress').value=ended?job.progress||0:Math.min(job.progress||0,99);$('graph-message').textContent=serverText(job.phase);$('graph-message').title='';window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));
      // The stage comes from the server when it reports one; preparing evidence is always the first.
      this.stages(job.graph_stage in STAGE?STAGE[job.graph_stage]:this.action==='prepare'?0:null);
      // Joining reports its own three steps: they are shown under its button while the job lasts and once it has saved.
      if(job.graph_reconciliation)this.paintJoin($('graph-join'),job.graph_reconciliation);
      if(['done','error','cancelled'].includes(job.status)){sessionStorage.removeItem(STORE);if(job.error_info||job.error){$('graph-message').textContent=serverText(job.error_info?.message||job.error);$('graph-message').title=job.error_info?.detail||'';}await this.refresh();return job;}await new Promise(resolve=>setTimeout(resolve,1500));}
    }catch(error){$('graph-message').textContent=error.message;if(error.status===404){sessionStorage.removeItem(STORE);await this.refresh();}}finally{this.job=null;this.busy=false;this.action=null;this.stages(null);$('graph-progress').hidden=true;this.controls();}
  }
}
