import {$,node,toast,number} from './ui.js';
import {t,serverText,intlLocale} from './i18n.js';
import {collectionUrl,sessionKey} from './collection-scope.js';
import {GraphMap} from './graph-map.js';
const label=(key,values={})=>t(`graph.${key}`,values),STORE=sessionKey('musa-graph-job');
export class Graph{
  constructor(api){
    this.api=api;this.busy=false;this.active=false;
    this.map=new GraphMap();
    $('graph-select-all').addEventListener('click',()=>{for(const option of $('graph-files').options)option.selected=true;});
    $('graph-prepare').addEventListener('click',()=>this.start('prepare'));
    $('graph-extract').addEventListener('click',()=>this.start('process'));
    $('graph-audit').addEventListener('click',()=>this.audit());
    $('graph-cancel').addEventListener('click',async()=>{if(!this.job)return;try{await this.api('/api/jobs/'+this.job,{method:'DELETE'});}catch(error){toast(error.message);}});
    $('graph-provider').addEventListener('change',()=>{$('graph-remote-field').hidden=$('graph-provider').value==='ollama';$('graph-remote').checked=false;this.loadModels();});
    $('graph-model-refresh').addEventListener('click',()=>this.loadModels());
    $('graph-unit-limit').addEventListener('input',()=>this.batchHint());
    $('graph-verification-close').addEventListener('click',()=>$('graph-verification-dialog').close());
    $('graph-verification-dialog').addEventListener('close',()=>this.verifyTrigger?.focus());
    $('graph-verification-form').addEventListener('submit',async event=>{event.preventDefault();try{await this.api('/api/graph/assertions/'+this.verifyRow.id+'/verification',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:$('graph-verification-status').value,note:$('graph-verification-note').value})});$('graph-verification-dialog').close();await this.refresh();}catch(error){toast(error.message);}});
    window.addEventListener('rag:languagechange',()=>{if(this.snapshot)this.render(this.snapshot);});
  }
  setStatus(status){
    const files=$('graph-files'),selected=new Set([...files.selectedOptions].map(option=>option.value));
    const names=status.documents.filter(document=>/\.(pdf|md|txt|markdown)$/i.test(document.name)).map(document=>document.name);
    const signature=JSON.stringify(names);if(this.signature===signature)return;this.signature=signature;
    files.replaceChildren(...names.map(name=>{const option=node('option','',name);option.value=name;option.selected=selected.has(name);return option;}));
  }
  async setActive(active){this.active=active;if(!active)return;try{await this.refresh();const job=sessionStorage.getItem(STORE);if(job&&!this.busy)this.follow(job);}catch(error){toast(error.message);}}
  async refresh(){this.snapshot=await this.api('/api/graph');this.render(this.snapshot);const result=await this.api('/api/graph/audit');if(result.audit){const data=result.audit;$('graph-audit-summary').textContent=label('audit_summary',{checked:number(data.checked_documents),total:number(data.total_documents),issues:number(data.issues.length),duplicates:number(data.duplicate_groups.length)})+' · '+new Date(data.completed).toLocaleString(intlLocale());if(data.source_assessments)$('graph-audit-relations').textContent=label('audit_relations',data.source_assessments);$('graph-audit-report').href=collectionUrl('/api/graph/audit/report');$('graph-audit-report').hidden=false;}}
  async audit(){if(this.busy)return;this.busy=true;this.controls();try{const queued=await this.api('/api/graph/audit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all_collections:true})});sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);this.busy=false;this.controls();}}
  async loadModels(){
    const provider=$('graph-provider').value,previous=$('graph-model').value,sequence=(this.modelSequence||0)+1;this.modelSequence=sequence;
    try{const data=await this.api(provider==='ollama'?'/api/settings':'/api/providers/'+provider+'/models');if(sequence!==this.modelSequence)return;
      const names=data.models.map(row=>typeof row==='string'?row:row.name);$('graph-model').replaceChildren(node('option','',label('select_model')),...names.map(name=>{const option=node('option','',name);option.value=name;return option;}));$('graph-model').firstElementChild.value='';
      if(names.includes(previous))$('graph-model').value=previous;else if(names.includes(this.snapshot?.extraction_settings?.model))$('graph-model').value=this.snapshot.extraction_settings.model;
    }catch(error){toast(error.message);}
  }
  render(data){
    if(!this.modelInitialized){this.modelInitialized=true;if(data.extraction_settings?.provider)$('graph-provider').value=data.extraction_settings.provider;$('graph-remote-field').hidden=$('graph-provider').value==='ollama';this.loadModels();}
    $('graph-summary').textContent=label('summary',{documents:number(data.documents),units:number(data.units),modelled:number(data.modelled_units),assertions:number(data.assertions)});
    this.map.render(data.rows);this.batchHint();
    $('graph-results').replaceChildren(...data.rows.map(row=>{
      const article=node('article');article.append(node('strong','',`${row.subject_label} → ${row.predicate} → ${row.object_label}`),node('p','meta',`${label(row.basis)} · ${label(row.polarity)} · ${label(row.review)} · ${label('verification_'+row.verification)}`),node('blockquote','',row.quote));if(row.verification_note)article.append(node('p','meta',row.verification_note));
      const source=node('button','btn btn--secondary',row.file+(row.page?' · '+row.page:''));source.type='button';
      source.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('rag:document',{detail:{url:collectionUrl('/api/graph/source?unit='+row.unit),file:row.file,page:row.page||1,trigger:source}})));
      const verify=node('button','btn btn--secondary',label('verify'));verify.type='button';verify.addEventListener('click',()=>this.verify(row,verify));article.append(source,verify);for(const state of ['accepted','rejected','proposed']){const button=node('button','btn btn--secondary',label('review_'+state));button.type='button';button.disabled=row.review===state||(state==='accepted'&&row.verification!=='supported');button.addEventListener('click',async()=>{try{await this.api('/api/graph/assertions/'+row.id+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state})});await this.refresh();}catch(error){toast(error.message);}});article.append(button);}return article;
    }));
    if(!data.rows.length)$('graph-results').append(node('p','meta',label('empty')));
    if(data.truncated)$('graph-results').append(node('p','meta',label('truncated',{limit:data.limit})));
    $('graph-runs').replaceChildren(...data.runs.map(run=>{const row=node('div');row.append(node('span','meta',run.id.slice(0,10)+' · '+run.status));if(run.error)row.append(node('span','',run.error));
      if(['error','interrupted','cancelled'].includes(run.status)){const button=node('button','btn btn--secondary',label('resume'));button.type='button';button.disabled=this.busy;button.addEventListener('click',()=>this.resume(run.id));row.append(button);}return row;}));
  }
  async start(action){
    if(this.busy)return;
    let files=[...$('graph-files').selectedOptions].map(option=>option.value);if(!files.length&&action==='process')files=[...$('graph-files').options].map(option=>option.value);if(!files.length){toast(label('select_files'));return;}
    if(files.length>100){$('graph-options').open=true;toast(label('max_files'));return;}
    const body={files};if(action==='extract'||action==='process'){
      if(!$('graph-unit-limit').reportValidity())return;
      Object.assign(body,{provider:$('graph-provider').value,model:$('graph-model').value.trim(),max_units:Number($('graph-unit-limit').value),remote_content:$('graph-remote').checked});
      if(!body.model){$('graph-options').open=true;toast(label('select_model'));return;}
      if(body.provider!=='ollama'&&!body.remote_content){$('graph-options').open=true;toast(label('remote'));return;}
    }
    this.busy=true;this.controls();
    try{const queued=await this.api('/api/graph/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);this.busy=false;this.controls();}
  }
  batchHint(){$('graph-batch-hint').textContent=label('batch_hint',{limit:$('graph-unit-limit').value});}
  async verify(row,trigger){try{const result=await this.api('/api/graph/evidence/'+row.unit);this.verifyRow=row;this.verifyTrigger=trigger;$('graph-verification-claim').textContent=`${row.subject_label} → ${row.predicate} → ${row.object_label}`;$('graph-verification-evidence').textContent=result.text;$('graph-verification-note').value=row.verification_note||'';$('graph-verification-status').value=row.verification==='pending'?'':row.verification;$('graph-verification-dialog').showModal();}catch(error){toast(error.message);}}
  async resume(run){if(this.busy)return;try{const queued=await this.api('/api/graph/runs/'+run+'/resume',{method:'POST'});sessionStorage.setItem(STORE,queued.job_id);await this.follow(queued.job_id);}catch(error){toast(error.message);}}
  controls(){for(const id of ['graph-prepare','graph-extract','graph-select-all','graph-audit'])$(id).disabled=this.busy;$('graph-cancel').hidden=!this.busy;}
  async follow(id){
    this.job=id;this.busy=true;this.controls();$('graph-progress').hidden=false;
    try{while(true){const job=await this.api('/api/jobs/'+id);$('graph-progress').value=job.progress||0;$('graph-message').textContent=serverText(job.phase);window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));
      if(['done','error','cancelled'].includes(job.status)){sessionStorage.removeItem(STORE);if(job.error_info)$('graph-message').textContent=job.error_info.detail||serverText(job.error_info.message);await this.refresh();break;}await new Promise(resolve=>setTimeout(resolve,1500));}
    }catch(error){$('graph-message').textContent=error.message;if(error.status===404){sessionStorage.removeItem(STORE);await this.refresh();}}finally{this.job=null;this.busy=false;this.controls();}
  }
}
