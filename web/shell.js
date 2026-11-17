import {$,number,node} from './ui.js';
import {t,serverText,intlLocale} from './i18n.js';
import {CommandPalette} from './command-palette.js';
import {vramLevel} from './system-state.js';
import {ingestionState,selectIngestionJob} from './ingestion-state.js';
import {PipelineScene} from './pipeline-scene.js';
const h=(key,values={})=>t(`header.${key}`,values),s=(key,values={})=>t(`sentinel.${key}`,values);
const compact=value=>new Intl.NumberFormat(intlLocale(),{maximumFractionDigits:1}).format(value);
const viewStages={query:[5,6],library:[1,2,3,4],settings:[2,3,5,6],statistics:[4],console:[],laboratory:[3,4,5]};
// Where each stage of the explained scene is set: a card of the Settings view.
const card=(anchor,key,note)=>({anchor,key,note,element:()=>document.getElementById(anchor).closest('section')});
const index=()=>card('settings-index-title','scene.go_index','scene.note_fixed');
const place=(anchor,key)=>({anchor,key,element:()=>document.getElementById(anchor).closest('section')}),settings=()=>({href:'/configuracion',key:'scene.go_configuration'});
const sceneTargets={laboratory:{1:settings(),2:settings(),3:settings(),4:settings(),5:settings()},library:{1:place('upload-card-head','scene.go_upload'),2:settings(),3:settings(),4:place('library-location','scene.go_documents'),5:settings(),6:settings()},settings:{1:card('settings-multimodal-title','scene.go_settings'),2:index(),3:index(),4:index(),5:card('settings-retrieval-title','scene.go_settings'),6:card('settings-generation-title','scene.go_settings')}};
export class Shell {
  constructor(api){
    Object.assign(this,{api,status:null,settings:null,view:'query',loading:false,finished:new Set()});
    this.palette=new CommandPalette(()=>this.status);
    // Light theme: the isometric scene replaces the pipeline row and shows the same states and values.
    this.scenes=[...document.querySelectorAll('[data-pipeline-scene]')].map(host=>{const targets=sceneTargets[host.dataset.pipelineScene];const scene=new PipelineScene(host,targets?{explain:{targets}}:{});scene.kind=host.dataset.pipelineScene;return scene;});
    // Closing with ×, Esc or a click on empty space returns focus to the icon; a click on another control keeps it there.
    $('system-popover').addEventListener('toggle',event=>{$('system-button').setAttribute('aria-expanded',String(event.newState==='open'));const active=document.activeElement;if(event.newState==='closed'&&(this.systemFocus||!active||active===document.body))$('system-button').focus({preventScroll:true});this.systemFocus=false;});
    $('system-close').addEventListener('click',()=>{this.systemFocus=true;});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('system-popover').matches(':popover-open')){this.systemFocus=true;$('system-popover').hidePopover();}});
    $('system-popover').querySelectorAll('a,button').forEach(control=>control.addEventListener('click',()=>{if(control.id!=='refresh-button')$('system-popover').hidePopover();}));
    window.addEventListener('rag:languagechange',()=>{this.render();if(this.telemetry)this.hardware(this.telemetry);});
    window.addEventListener('rag:telemetry',event=>this.hardware(event.detail));
    window.addEventListener('rag:job',event=>{this.liveJob=event.detail;if(['done','error','cancelled'].includes(this.liveJob.status)){this.finished.add(this.liveJob.id);clearTimeout(this.jobTimer);this.jobTimer=setTimeout(()=>{this.liveJob=null;this.render();},1500);}this.render();});
    window.addEventListener('rag:queue',event=>{this.queue=event.detail;this.badge();this.pipeline();});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
    let last=scrollY;window.addEventListener('scroll',()=>{const current=scrollY;$('topbar').classList.toggle('is-compact',current>120&&current>last);last=current;},{passive:true});this.schedule(0);
  }
  setStatus(status){this.status={...status,jobs:status.global_jobs||status.jobs};this.render();}
  setSettings(value){this.settings=value;this.render();}
  setView(view){this.view=view;this.pipeline();}
  render(){
    const status=this.status;if(!status)return;
    const llm=this.settings?.response_provider&&this.settings.response_provider!=='ollama'?`${this.settings.response_provider} · ${this.settings.external_model||'—'}`:this.settings?.response_mode==='manual'?this.settings.response_model:s('auto');
    const values={'pipeline-docs':s('document_count',{count:number(status.document_count)}),'pipeline-chunks':s('chunk_value',{size:status.chunk_size,overlap:status.chunk_overlap}),'pipeline-embedding':status.embedding_model.split('/').at(-1)+' · '+status.embedding_device.toUpperCase(),'pipeline-vectors':status.collection+' · '+number(status.chunk_count),'pipeline-retrieval':'top-k '+(this.settings?.k??4),'pipeline-llm':llm};
    for(const [id,value]of Object.entries(values))$(id).textContent=value;
    $('system-ollama').textContent=s(status.ollama.connected?'connected':'offline');$('system-ollama-tag').classList.toggle('is-danger',!status.ollama.connected);
    const loaded=status.ollama.loaded||[],models=loaded.map(item=>item.name).join(' · ');$('system-models').textContent=models||h('no_model');$('system-models').title=models;$('system-models').classList.toggle('is-empty',!models);
    $('system-embeddings').textContent=$('system-embeddings').title=status.embedding_model;$('system-embeddings-device').textContent=status.embedding_device.toUpperCase();
    const host=status.server?.bind_host,local=status.server?.access==='loopback',port=location.port||(location.protocol==='https:'?'443':'80');
    $('exposure-detail').textContent=host?h(local?'local':'lan'):'—';$('server-exposure-tag').classList.toggle('is-warn',Boolean(host)&&!local);
    $('pill-ollama').dataset.level=status.ollama.connected?'ok':'crit';$('pill-network').textContent=host?h(local?'local_short':'lan'):'';$('pill-network').hidden=!host;$('pill-network').classList.toggle('is-warn',Boolean(host)&&!local);
    $('server-address').textContent=host?host+':'+port:location.host;$('server-exposure-icon').setAttribute('href',local?'#i-lock':'#i-wifi');
    $('config-dimensions').textContent=status.embedding_dimensions==null?'—':number(status.embedding_dimensions);this.badge();this.pipeline();this.level();
  }
  badge(){const badge=$('nav-badge'),status=this.status;if(!status)return;const active=(status.jobs||[]).filter(job=>job.kind==='index'&&['queued','running'].includes(job.status)).length,work=Math.max(active,this.queue?.pending||0),errors=status.documents.filter(item=>item.status==='error').length;badge.hidden=!work&&!errors;badge.className='topnav__badge '+(errors?'topnav__badge--error':'topnav__badge--work');badge.textContent=errors?h('errors',{count:errors}):h('queued',{count:work});}
  level(){const device=this.telemetry?.gpu?.devices?.[0],level=vramLevel(device?.memory_used_bytes,device?.memory_total_bytes,this.status?.ollama.loaded?.length||0);$('system-vram-row').dataset.level=$('pill-vram').dataset.level=level;$('system-button').dataset.level=this.status?.ollama.connected===false?'crit':level==='crit'?'crit':level==='warn'||this.status?.server?.access==='lan'?'warn':'info';}
  hardware(data){
    this.telemetry=data;const device=data?.gpu?.devices?.[0],used=device?.memory_used_bytes,total=device?.memory_total_bytes,ratio=total?Math.max(0,Math.min(100,100*used/total)):null;
    $('header-vram').textContent=total?`${number(used/1024**3,1)} / ${number(total/1024**3,1)} GiB`:'—';
    // A reading that did not arrive hides its row instead of showing a placeholder.
    $('pill-vram').hidden=ratio==null;$('pill-vram').textContent=ratio==null?'':'VRAM '+number(ratio)+' %';
    const bar=$('system-vram-progress');$('system-vram-row').hidden=ratio==null;bar.style.setProperty('--pct',(ratio??0)+'%');bar.classList.toggle('is-warn',ratio!=null&&ratio>=90);bar.setAttribute('aria-valuemax',total?(total/1024**3).toFixed(1):'0');bar.setAttribute('aria-valuenow',total?(used/1024**3).toFixed(1):'0');
    $('system-gpu-row').hidden=!device;$('system-gpu').textContent=$('system-gpu').title=device?.name||'';
    const metrics=device?[[device.utilization_percent,' %',h('usage')],[device.temperature_c,' °C',''],[device.power_w,' W','']].filter(([value])=>value!=null).map(([value,unit,label])=>{const item=node('span');item.append(node('b','',compact(value)+unit));if(label)item.append(' '+label);return item;}):[];
    $('system-gpu-metrics').replaceChildren(...metrics);$('system-gpu-metrics').hidden=!metrics.length;
    // The processes of the RAG server and Ollama, with the readings of the same sample.
    const processes=(data?.processes||[]).map(process=>{const row=node('li','sys__process');row.append(node('span','sys__process-role',process.role==='rag'?t('ui.rag_server_1f279b'):'Ollama'),node('span','sys__process-use',`CPU ${process.cpu_percent==null?'—':number(process.cpu_percent,1)+' %'} · RAM ${number(process.ram_bytes/1024**3,2)} GiB`),node('span','sys__process-name',`${process.name} · PID ${process.pid}`));return row;});
    $('hardware-processes').replaceChildren(...processes);$('system-processes-row').hidden=!processes.length;this.level();
  }
  ingestion(job){
    const state=ingestionState(job),active=job&&['running','queued'].includes(job.status);
    const recent=job&&Date.now()-new Date(job.updated_at).getTime()<10000;
    const inMenu=Boolean(state&&(active||recent));
    $('system-ingestion').hidden=!state;$('ingestion-menu').hidden=!inMenu;
    $('pipeline-live').classList.toggle('has-ingestion',inMenu);
    if(!state)return;
    const phase=t(`ingestion.phase_${state.phase}`),pages=state.pages;
    const pageText=pages?t('ingestion.pages',{done:number(pages.completed),total:number(pages.total)}):'';
    const percentage=state.percent==null?'…':number(state.percent)+' %';
    const label=phase+(pageText?' · '+pageText:'');
    $('system-ingestion-document').textContent=state.document||t('ingestion.title');
    $('system-ingestion-percent').textContent=$('ingestion-menu-percent').textContent=percentage;
    $('system-ingestion-phase').textContent=label;
    $('ingestion-menu-label').textContent=label;
    $('ingestion-menu').title=state.document+' · '+label;
    $('ingestion-menu').setAttribute('aria-label',t('ingestion.open')+' · '+label+' · '+percentage);
    $('ingestion-menu').dataset.state=job.status;
    const detail=[];
    if(pages){
      if(pages.phase==='vision'&&pages.completed<pages.total)detail.push(t('ingestion.current_page',{page:number(pages.page)}));
      if(pages.visuals_total>0)detail.push(t('ingestion.visuals',{done:number(pages.visuals_completed),total:number(pages.visuals_total),cached:number(pages.cached_visuals)}));
    }
    if(job.error_info?.message||job.error)detail.push(serverText(job.error_info?.message||job.error));
    $('system-ingestion-detail').textContent=detail.join(' · ');
    const bar=$('system-ingestion-bar'),track=$('ingestion-menu-track');
    if(state.percent==null){bar.removeAttribute('value');track.removeAttribute('aria-valuenow');}
    else{bar.value=state.percent;track.setAttribute('aria-valuenow',String(state.percent));}
    track.setAttribute('aria-valuetext',label+' · '+percentage);
    track.classList.toggle('is-indeterminate',state.percent==null);
    $('ingestion-menu-fill').style.width=(state.percent??35)+'%';
  }
  pipeline(){
    if(!this.status)return;
    this.ingestion(selectIngestionJob(this.status.ingestion,this.status.jobs||[],this.liveJob));
    const job=this.liveJob||(this.status.jobs||[]).find(item=>item.status==='running'&&!this.finished.has(item.id))||(this.status.jobs||[]).find(item=>item.status==='queued'&&!this.finished.has(item.id)),states=Array(6).fill('idle');let caption=h('idle');
    if(!job){for(const number of viewStages[this.view]||[])states[number-1]='rel';}
    else if(job.status==='queued')caption=h('queue_wait');
    else{
      const terminal=['done','error','cancelled'].includes(job.status),failed=job.status==='error';let running=[];
      if(job.kind==='query'){if(job.metrics?.routing_mode!=='catalog'){const llm=job.model||job.metrics?.model;running=llm?[5]:[4];if(llm)states[4]='done';if(job.metrics?.routing_mode==='search_only')running=[4];}caption=terminal?h(job.status==='done'?'completed':failed?'failed':'cancelled'):h('query_elapsed',{seconds:number(job.elapsed_seconds||0,1)});}
      else if(job.kind==='prepare'){running=[2];caption=serverText(job.phase)||'—';}
      else if(job.kind==='evaluation'){running=[4];caption=serverText(job.phase)||'—';}
      else{running=job.stage==='indexing'?[2,3]:job.stage==='upload'?[0]:[1];for(let index=0;index<running[0];index++)states[index]='done';caption=terminal?h(job.status==='done'?'completed':failed?'failed':'cancelled'):h('index_progress',{progress:job.progress==null?'—':number(job.progress)});}
      for(const index of running)states[index]=terminal?(failed?'error':job.status==='done'?'done':'idle'):'run';
      if(job.kind==='query'&&job.status==='done'&&job.metrics?.routing_mode!=='catalog'){states[4]='done';if(!job.search_only)states[5]='done';}
    }
    if(!job&&this.queue?.uploading){states.fill('idle');states[0]='run';caption=h('upload_progress',{progress:number(this.queue.percent||0)});}
    if(job?.kind==='query'&&job.sources?.length)$('pipeline-retrieval').textContent=s('sources_ready',{count:job.sources.length});
    if(job?.kind==='query'&&(job.model||job.metrics?.model))$('pipeline-llm').textContent=(job.model||job.metrics.model)+(job.metrics?.generation_tok_s?' · '+number(job.metrics.generation_tok_s,1)+' tok/s':'');
    if(job?.kind==='index'&&job.progress!=null){const label=number(job.progress)+' %';for(const index of [2,3])if(states[index]==='run')document.querySelectorAll('.pstep__value')[index].textContent=label;}
    for(const [index,element]of [...document.querySelectorAll('.pstep')].entries()){element.dataset.state=states[index];const value=element.querySelector('.pstep__value');value.title=index===2?this.status.embedding_model+' · '+this.status.embedding_device:value.textContent;const label=element.querySelector('.pstep__name').textContent+' · '+value.title;element.querySelector('a').setAttribute('aria-label',label);}
    const shown=[...document.querySelectorAll('.pstep__value')].map(value=>value.textContent);for(const scene of this.scenes){
      // The Laboratory measures retrieval only: its scene says so on the model stage.
      if(scene.kind!=='laboratory'){scene.set(states,shown);continue;}
      const own=[...shown];own[4]='top-k '+(this.settings?.k??4)+(this.status.reranker?.model?' · '+t('lab.reranked'):'');own[5]=t('lab.model_unused');scene.set(states.map((state,index)=>index===5?'idle':state),own);
    }
    $('pipeline-state').textContent=caption;$('pipeline-status').classList.toggle('is-busy',states.includes('run'));$('pipeline-live').setAttribute('aria-label',h('pipeline')+' · '+caption);
  }
  schedule(delay=2000){clearTimeout(this.timer);if(!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){if(this.loading)return;this.loading=true;try{if(this.view!=='statistics')this.hardware(await this.api('/api/telemetry?history_seconds=60'));const shared=await this.api('/api/jobs?active=1&all_collections=1');if(this.status){this.status.jobs=shared.jobs;this.render();}}catch{}finally{this.loading=false;this.schedule();}}
}
