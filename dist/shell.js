import {$,number,node,button} from './ui.js';
import {t} from './i18n.js';
const s=(key,values={})=>t(`sentinel.${key}`,values);

export class Shell {
  constructor(api){this.api=api;this.status=null;this.settings=null;this.timer=null;this.loading=false;window.addEventListener('rag:languagechange',()=>this.render());window.addEventListener('rag:telemetry',event=>this.hardware(event.detail));document.addEventListener('visibilitychange',()=>this.schedule(0));this.schedule(0);}
  setStatus(status){this.status=status;this.render();}
  setSettings(value){this.settings=value;this.render();}
  setView(view){const stages={query:[5,6],library:[1,2,3,4],settings:[2,3,5,6],statistics:[4],console:[]};for(const step of document.querySelectorAll('[data-pipeline]'))step.classList.toggle('is-active',(stages[view]||[]).includes(Number(step.dataset.pipeline)));}
  render(){
    const status=this.status,settings=this.settings;if(!status)return;
    $('pipeline-docs').textContent=s('document_count',{count:number(status.document_count)});
    $('pipeline-chunks').textContent=s('chunk_value',{size:status.chunk_size,overlap:status.chunk_overlap});
    $('pipeline-embedding').textContent=status.embedding_model.split('/').at(-1)+' · '+status.embedding_device.toUpperCase();
    $('pipeline-vectors').textContent=status.collection+' · '+number(status.chunk_count);
    $('pipeline-retrieval').textContent='top-k '+(settings?.k??4);
    $('pipeline-llm').textContent=settings?.response_mode==='manual'?settings.response_model:s('auto');
    $('ollama-header').textContent=s(status.ollama.connected?'ollama_connected':'ollama_offline');
    $('stat-engine').querySelector('.dot').className='dot '+(status.ollama.connected?'dot--ok':'dot--err');
    $('stat-engine').title=s('ollama_hint');
    const locality=$('locality-label'),local=['localhost','127.0.0.1','testserver'].includes(location.hostname);locality.dataset.i18n='sentinel.'+(local?'local':'trusted_lan');locality.textContent=s(local?'local':'trusted_lan');
    $('config-dimensions').textContent=status.embedding_dimensions==null?'—':number(status.embedding_dimensions);
  }
  hardware(data){const device=data?.gpu?.devices?.[0];$('header-vram').textContent=device?.memory_total_bytes?`${number(device.memory_used_bytes/1024**3,1)} / ${number(device.memory_total_bytes/1024**3,1)} GiB`:'—';$('header-vram-bar').style.width=device?.memory_total_bytes?Math.max(0,Math.min(100,100*device.memory_used_bytes/device.memory_total_bytes))+'%':'0%';}
  schedule(delay=10000){clearTimeout(this.timer);if(!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){if(this.loading)return;this.loading=true;try{this.hardware(await this.api('/api/telemetry?history_seconds=60'));}catch{}finally{this.loading=false;this.schedule();}}
}
