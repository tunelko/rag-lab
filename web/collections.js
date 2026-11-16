import {$,node,toast} from './ui.js';
import {t} from './i18n.js';
import {collectionId} from './collection-scope.js';
const label=(key,values={})=>t(`collections.${key}`,values);
export class Collections{
  constructor(api,refresh){
    this.api=api;this.refresh=refresh;this.records=[];
    $('collection-select').addEventListener('change',()=>this.select($('collection-select').value));
    $('cti-import').addEventListener('click',()=>this.import());
    window.addEventListener('rag:languagechange',()=>this.render());
  }
  async load(){const result=await this.api('/api/collections');this.records=result.collections;this.render();if(collectionId==='cti'){const dataset=await this.api('/api/datasets');this.dataset=dataset.status;this.render();const job=sessionStorage.getItem('musa:cti-job');if(job&&!this.running)this.follow(job);}}
  render(){
    $('collection-select').replaceChildren(...this.records.map(row=>{const option=node('option','',row.id==='default'?label('default'):row.name);option.value=row.id;return option;}));
    $('collection-select').value=collectionId;
    $('cti-limit-field').hidden=collectionId!=='cti';
    $('cti-summary').hidden=collectionId!=='cti';
    if(collectionId==='cti')$('cti-summary').textContent=this.dataset?.status==='done'?label('summary',{documents:this.dataset.semantic_documents,relations:this.dataset.statistics.active_relationships}):label('sample_hint');
    $('cti-import').disabled=Boolean(this.running);
    $('cti-import').querySelector('span').textContent=label(collectionId==='cti'?'sync_cti':'import_cti');
  }
  select(id){sessionStorage.setItem('musa:collection',id);const url=new URL(location.href);if(id==='default')url.searchParams.delete('collection');else url.searchParams.set('collection',id);location.assign(url);}
  async import(){
    const control=$('cti-import');control.disabled=true;
    try{
      if(collectionId!=='cti'){
        await this.api('/api/collections',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:'cti',name:'CTI · APTs',adapter:'cti'})});
        this.select('cti');return;
      }
      if(!$('cti-limit').reportValidity())return;
      const queued=await this.api('/api/datasets/cti/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({attack_version:'20.1',max_documents:Number($('cti-limit').value)})});
      sessionStorage.setItem('musa:cti-job',queued.job_id);
      toast(label('started'));this.follow(queued.job_id);await this.refresh();
    }catch(error){toast(error.message);}finally{control.disabled=Boolean(this.running);}
  }
  async follow(id){
    this.running=true;this.render();
    try{
      while(true){const job=await this.api('/api/jobs/'+encodeURIComponent(id));window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));
        if(['done','error','cancelled','interrupted'].includes(job.status)){sessionStorage.removeItem('musa:cti-job');await this.refresh();this.dataset=(await this.api('/api/datasets')).status;if(job.status==='error')toast(job.error_info?.detail||job.error_info?.message);break;}
        await new Promise(resolve=>setTimeout(resolve,2500));}
    }catch(error){toast(error.message);}finally{this.running=false;this.render();}
  }
}
