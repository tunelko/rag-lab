// Screen logic of the Graph view: sources, extraction options, batch progress, assessment figures and the
// register of relations. The canvas and the evidence panel are in graph-map.js; the server decides everything else.
import {$,node,toast,number} from './ui.js';
import {t,serverText,intlLocale} from './i18n.js';
import {collectionUrl,sessionKey} from './collection-scope.js';
import {GraphMap} from './graph-map.js';
const label=(key,values={})=>t(`graph.${key}`,values),STORE=sessionKey('musa-graph-job');
const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
// The server reports finer stages than the three the screen shows: publishing and reconciling belong to the last one.
const STAGE={preparation:0,extraction:1,assessment:2,publication:2,reconciliation:2};
export class Graph{
  constructor(api){
    this.api=api;this.busy=false;this.active=false;this.audit_=null;
    this.map=new GraphMap();
    $('graph-select-all').addEventListener('click',()=>{for(const box of this.boxes())box.checked=true;this.sources();});
    $('graph-files').addEventListener('change',()=>this.sources());
    $('graph-prepare').addEventListener('click',()=>this.start('prepare'));
    $('graph-extract').addEventListener('click',()=>this.start('process'));
    $('graph-audit').addEventListener('click',()=>this.audit());
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
    window.addEventListener('rag:languagechange',()=>{if(this.snapshot)this.render(this.snapshot);this.sources();this.renderAudit();});
    this.stages(null);this.controls();
  }
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
  async refresh(){this.snapshot=await this.api('/api/graph');this.render(this.snapshot);const result=await this.api('/api/graph/audit');this.audit_=result.audit||null;this.renderAudit();}
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
    const body={files};if(action==='extract'||action==='process'){
      if(!$('graph-unit-limit').reportValidity())return;
      Object.assign(body,{provider:$('graph-provider').value,model:$('graph-model').value.trim(),max_units:Number($('graph-unit-limit').value),remote_content:$('graph-remote').checked});
      if(!body.model){$('graph-model').focus();toast(label('select_model'));return;}
      if(body.provider!=='ollama'&&!body.remote_content){$('graph-remote').focus();toast(label('remote'));return;}
    }
    this.busy=true;this.action=action;this.controls();
    try{const queued=await this.api('/api/graph/'+action,post(body));sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);this.busy=false;this.controls();}
  }
  batchHint(){$('graph-batch-hint').textContent=label('batch_hint',{limit:$('graph-unit-limit').value});}
  async verify(row,trigger){try{const result=await this.api('/api/graph/evidence/'+row.unit);this.verifyRow=row;this.verifyTrigger=trigger;$('graph-verification-claim').textContent=`${row.subject_label} → ${row.predicate} → ${row.object_label}`;$('graph-verification-evidence').textContent=result.text;$('graph-verification-note').value=row.verification_note||'';$('graph-verification-status').value=row.verification==='pending'?'':row.verification;$('graph-verification-dialog').showModal();}catch(error){toast(error.message);}}
  async resume(run){if(this.busy)return;try{const queued=await this.api('/api/graph/runs/'+run+'/resume',{method:'POST'});sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);}}
  // Without a model there is nothing to extract with: the field says so and the main action waits.
  controls(){
    const missing=!$('graph-model').value;
    for(const id of ['graph-prepare','graph-select-all','graph-audit'])$(id).disabled=this.busy;
    $('graph-extract').disabled=this.busy||missing;$('graph-cancel').hidden=!this.busy;
    $('graph-model').classList.toggle('is-missing',missing);$('graph-model-reason').hidden=!missing;
  }
  // The three stages of a batch: the one working in lime, the finished ones in mint.
  stages(current){[...$('graph-stages').children].forEach((item,index)=>{const now=current===index;item.classList.toggle('is-current',now);item.classList.toggle('is-done',current!=null&&index<current);if(now)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});}
  async follow(id){
    this.job=id;this.busy=true;this.controls();$('graph-progress').hidden=false;
    try{while(true){const job=await this.api('/api/jobs/'+id);$('graph-progress').value=job.progress||0;$('graph-message').textContent=serverText(job.phase);window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));
      // The stage comes from the server when it reports one; preparing evidence is always the first.
      this.stages(job.graph_stage in STAGE?STAGE[job.graph_stage]:this.action==='prepare'?0:null);
      if(['done','error','cancelled'].includes(job.status)){sessionStorage.removeItem(STORE);if(job.error_info)$('graph-message').textContent=job.error_info.detail||serverText(job.error_info.message);await this.refresh();break;}await new Promise(resolve=>setTimeout(resolve,1500));}
    }catch(error){$('graph-message').textContent=error.message;if(error.status===404){sessionStorage.removeItem(STORE);await this.refresh();}}finally{this.job=null;this.busy=false;this.action=null;this.stages(null);$('graph-progress').hidden=true;this.controls();}
  }
}
