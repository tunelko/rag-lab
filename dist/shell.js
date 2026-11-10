import {$,number,node} from './ui.js';
import {t,serverText} from './i18n.js';
import {CommandPalette} from './command-palette.js';
import {vramLevel} from './system-state.js';
const h=(key,values={})=>t(`header.${key}`,values),s=(key,values={})=>t(`sentinel.${key}`,values);
const viewStages={query:[5,6],library:[1,2,3,4],settings:[2,3,5,6],statistics:[4],console:[]};
export class Shell {
  constructor(api){
    Object.assign(this,{api,status:null,settings:null,view:'query',loading:false,finished:new Set()});
    this.palette=new CommandPalette(()=>this.status);
    for(let i=1;i<=6;i++){$('system-pipeline-stages').append(node('li'));}
    $('pipeline-live').addEventListener('click',event=>{if(matchMedia('(max-width:639px)').matches&&event.target.closest('.pstep')){event.preventDefault();$('system-pipeline-detail').open=true;$('system-popover').showPopover();$('system-pipeline-detail').querySelector('summary').focus();}});
    $('system-popover').addEventListener('toggle',event=>{$('system-button').setAttribute('aria-expanded',String(event.newState==='open'));if(event.newState==='closed'&&this.systemFocus)$('system-button').focus({preventScroll:true});this.systemFocus=false;});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('system-popover').matches(':popover-open')){this.systemFocus=true;$('system-popover').hidePopover();}});
    $('system-popover').querySelectorAll('a,button').forEach(control=>control.addEventListener('click',()=>{if(control.id!=='refresh-button')$('system-popover').hidePopover();}));
    window.addEventListener('rag:languagechange',()=>{this.render();if(this.telemetry)this.hardware(this.telemetry);});
    window.addEventListener('rag:telemetry',event=>this.hardware(event.detail));
    window.addEventListener('rag:job',event=>{this.liveJob=event.detail;if(['done','error','cancelled'].includes(this.liveJob.status)){this.finished.add(this.liveJob.id);clearTimeout(this.jobTimer);this.jobTimer=setTimeout(()=>{this.liveJob=null;this.render();},1500);}this.render();});
    window.addEventListener('rag:queue',event=>{this.queue=event.detail;this.badge();this.pipeline();});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
    let last=scrollY;window.addEventListener('scroll',()=>{const current=scrollY;$('topbar').classList.toggle('is-compact',current>120&&current>last);last=current;},{passive:true});this.schedule(0);
  }
  setStatus(status){this.status=status;this.render();}
  setSettings(value){this.settings=value;this.render();}
  setView(view){this.view=view;this.pipeline();}
  render(){
    const status=this.status;if(!status)return;
    const values={'pipeline-docs':s('document_count',{count:number(status.document_count)}),'pipeline-chunks':s('chunk_value',{size:status.chunk_size,overlap:status.chunk_overlap}),'pipeline-embedding':status.embedding_model.split('/').at(-1)+' · '+status.embedding_device.toUpperCase(),'pipeline-vectors':status.collection+' · '+number(status.chunk_count),'pipeline-retrieval':'top-k '+(this.settings?.k??4),'pipeline-llm':this.settings?.response_mode==='manual'?this.settings.response_model:s('auto')};
    for(const [id,value]of Object.entries(values))$(id).textContent=value;
    $('ollama-header').textContent=s(status.ollama.connected?'ollama_connected':'ollama_offline');$('system-ollama').textContent=s(status.ollama.connected?'connected':'offline');
    const loaded=status.ollama.loaded||[];$('system-models').textContent=loaded.length?loaded.map(item=>item.name).join(' · '):h('no_model');$('system-embeddings').textContent=status.embedding_model+' · '+status.embedding_device.toUpperCase();
    const host=status.server?.bind_host,local=status.server?.access==='loopback';
    $('locality-label').textContent=host?h(local?'local':'lan'):'—';$('exposure-detail').textContent=host?h(local?'local_detail':'lan_detail'):'—';$('exposure-hint').textContent=host&&!local?h('restrict_lan'):'';
    $('server-address').textContent=location.host+(host?' · '+h('bind',{host}):'');$('server-exposure').dataset.level=host&&!local?'warn':'info';$('exposure-icon').setAttribute('href',local?'#i-lock':'#i-wifi');
    $('config-dimensions').textContent=status.embedding_dimensions==null?'—':number(status.embedding_dimensions);this.badge();this.pipeline();this.level();
  }
  badge(){const badge=$('nav-badge'),status=this.status;if(!status)return;const active=(status.jobs||[]).filter(job=>job.kind==='index'&&['queued','running'].includes(job.status)).length,work=Math.max(active,this.queue?.pending||0),errors=status.documents.filter(item=>item.status==='error').length;badge.hidden=!work&&!errors;badge.className='topnav__badge '+(errors?'topnav__badge--error':'topnav__badge--work');badge.textContent=errors?h('errors',{count:errors}):h('queued',{count:work});}
  level(){const device=this.telemetry?.gpu?.devices?.[0],level=vramLevel(device?.memory_used_bytes,device?.memory_total_bytes,this.status?.ollama.loaded?.length||0);$('system-vram').dataset.level=level;$('system-button').dataset.level=this.status?.ollama.connected===false?'crit':level==='crit'?'crit':level==='warn'||this.status?.server?.access==='lan'?'warn':'info';$('system-dot').dataset.level=this.status?.ollama.connected===false?'crit':'ok';}
  hardware(data){
    this.telemetry=data;const device=data?.gpu?.devices?.[0],used=device?.memory_used_bytes,total=device?.memory_total_bytes,ratio=total?Math.max(0,Math.min(100,100*used/total)):null;
    $('header-vram').textContent=total?`${number(used/1024**3,1)} / ${number(total/1024**3,1)} GiB`:'—';$('vram-percent').textContent=ratio==null?'—':number(ratio)+' %';$('header-vram-bar').style.width=(ratio??0)+'%';if(ratio==null)$('system-vram-progress').removeAttribute('value');else $('system-vram-progress').value=ratio;
    $('system-gpu').textContent=device?[device.name,device.utilization_percent==null?null:number(device.utilization_percent,1)+' %',device.temperature_c==null?null:number(device.temperature_c)+' °C',device.power_w==null?null:number(device.power_w,1)+' W'].filter(Boolean).join(' · '):'—';this.level();
  }
  pipeline(){
    if(!this.status)return;
    const job=this.liveJob||(this.status.jobs||[]).find(item=>item.status==='running'&&!this.finished.has(item.id))||(this.status.jobs||[]).find(item=>item.status==='queued'&&!this.finished.has(item.id)),states=Array(6).fill('idle');let caption=h('idle');
    if(!job){for(const number of viewStages[this.view]||[])states[number-1]='rel';}
    else if(job.status==='queued')caption=h('queue_wait');
    else{
      const terminal=['done','error','cancelled'].includes(job.status),failed=job.status==='error';let running=[];
      if(job.kind==='query'){if(job.metrics?.routing_mode!=='catalog'){const llm=job.model||job.metrics?.model;running=llm?[5]:[4];if(llm)states[4]='done';if(job.metrics?.routing_mode==='search_only')running=[4];}caption=terminal?h(job.status==='done'?'completed':failed?'failed':'cancelled'):h('query_elapsed',{seconds:number(job.elapsed_seconds||0,1)});}
      else if(job.kind==='prepare'){running=[2];caption=serverText(job.phase)||'—';}
      else{running=job.stage==='indexing'?[2,3]:job.stage==='upload'?[0]:[1];for(let index=0;index<running[0];index++)states[index]='done';caption=terminal?h(job.status==='done'?'completed':failed?'failed':'cancelled'):h('index_progress',{progress:job.progress==null?'—':number(job.progress)});}
      for(const index of running)states[index]=terminal?(failed?'error':job.status==='done'?'done':'idle'):'run';
      if(job.kind==='query'&&job.status==='done'&&job.metrics?.routing_mode!=='catalog'){states[4]='done';if(!job.search_only)states[5]='done';}
    }
    if(!job&&this.queue?.uploading){states.fill('idle');states[0]='run';caption=h('upload_progress',{progress:number(this.queue.percent||0)});}
    if(job?.kind==='query'&&job.sources?.length)$('pipeline-retrieval').textContent=s('sources_ready',{count:job.sources.length});
    if(job?.kind==='query'&&(job.model||job.metrics?.model))$('pipeline-llm').textContent=(job.model||job.metrics.model)+(job.metrics?.generation_tok_s?' · '+number(job.metrics.generation_tok_s,1)+' tok/s':'');
    if(job?.kind==='index'&&job.progress!=null){const label=number(job.progress)+' %';for(const index of [2,3])if(states[index]==='run')document.querySelectorAll('.pstep__value')[index].textContent=label;}
    for(const [index,element]of [...document.querySelectorAll('.pstep')].entries()){element.dataset.state=states[index];const value=element.querySelector('.pstep__value');value.title=index===2?this.status.embedding_model+' · '+this.status.embedding_device:value.textContent;const label=element.querySelector('.pstep__name').textContent+' · '+value.title;element.querySelector('a').setAttribute('aria-label',label);$('system-pipeline-stages').children[index].textContent=label;}
    $('pipeline-state').textContent=caption;$('pipeline-status').classList.toggle('is-busy',states.includes('run'));$('pipeline-live').setAttribute('aria-label',h('pipeline')+' · '+caption);
  }
  schedule(delay=2000){clearTimeout(this.timer);if(!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){if(this.loading)return;this.loading=true;try{if(this.view!=='statistics')this.hardware(await this.api('/api/telemetry?history_seconds=60'));}catch{}finally{this.loading=false;this.schedule();}}
}
