import {$, node, number} from './ui.js';
import {mergeHistory} from './chart-data.js';
import {ResourceChart} from './resource-charts.js';

const percent=value=>value==null?'—':`${number(value,1)} %`;
const memory=(used,total)=>used==null||total==null?'—':`${number(used/1024**3,1)} / ${number(total/1024**3,1)} GiB`;
export class Telemetry {
  constructor(api){
    this.api=api;this.active=false;this.loading=false;this.timer=null;this.history=[];this.window=60;this.paused=false;this.latest=null;this.display=null;this.rangeChanged=false;
    try{const saved=Number(localStorage.getItem('rag:hardware-range'));if([60,300,900,3600,86400,604800].includes(saved))this.window=saved;}catch{}
    $('hardware-range').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.seconds)===this.window)));
    this.charts=[['cpu','CPU'],['gpu','GPU'],['ram','RAM'],['vram','VRAM']].map(([key,label])=>new ResourceChart(`chart-${key}`,key,label));
    $('hardware-range').addEventListener('click',event=>{const control=event.target.closest('[data-seconds]');if(!control)return;this.window=Number(control.dataset.seconds);try{localStorage.setItem('rag:hardware-range',String(this.window))}catch{}this.history=[];this.rangeChanged=true;$('hardware-range').querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(button===control)));this.schedule(0);});
    $('hardware-pause').addEventListener('click',()=>{this.paused=!this.paused;$('hardware-pause').textContent=this.paused?'Reanudar':'Pausar gráficas';$('hardware-pause').setAttribute('aria-pressed',String(this.paused));if(this.paused)$('hardware-status').textContent='Visualización pausada · recogiendo lecturas';else if(this.latest)this.render(this.latest);});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
  }
  setActive(active){this.active=active;this.schedule(0);}
  schedule(delay=2000){clearTimeout(this.timer);if(this.active&&!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){
    if(this.loading||!this.active)return;this.loading=true;
    const requestedWindow=this.window;
    try{
      let data;try{data=await this.api(`/api/telemetry?history_seconds=${requestedWindow}`);}catch(error){if(error.status!==404)throw error;data=(await this.api('/assets/runtime-console.json')).telemetry;}
      if(!data)throw new Error('Preparando lecturas');
      if(requestedWindow!==this.window)return;
      this.history=mergeHistory(this.history,data);this.latest=data;
      if(!this.paused||this.rangeChanged)this.render(data);this.rangeChanged=false;
    }catch(error){if(!this.paused)$('hardware-status').textContent='Esperando lecturas de hardware…';}
    finally{this.loading=false;this.schedule(requestedWindow!==this.window?0:2000);}
  }
  render(data){
      $('hardware-cpu').textContent=percent(data.cpu_percent);$('hardware-cpu-caption').textContent=`Uso total del equipo · ${data.logical_cpus} procesadores lógicos`;
      $('hardware-ram').textContent=memory(data.ram_used_bytes,data.ram_total_bytes);$('hardware-ram-caption').textContent=percent(data.ram_percent)+' de la RAM física';
      const gpu=data.gpu.devices?.[0];$('hardware-gpu').textContent=percent(gpu?.utilization_percent);$('hardware-gpu-caption').textContent=gpu?.name||data.gpu.error||'GPU no disponible';
      $('hardware-gpu-sensors').textContent=gpu?[gpu.temperature_c==null?'':`Núcleo: ${number(gpu.temperature_c,1)} °C`,gpu.power_w==null?'':`${number(gpu.power_w,1)} W`].filter(Boolean).join(' · '):'';
      $('hardware-vram').textContent=gpu?memory(gpu.memory_used_bytes,gpu.memory_total_bytes):'—';
      $('hardware-vram-caption').textContent=gpu?.memory_total_bytes?percent(100*gpu.memory_used_bytes/gpu.memory_total_bytes)+' de la memoria dedicada':'Sin lectura de memoria GPU';
      $('hardware-processes').replaceChildren();
      for(const process of data.processes){const row=node('tr');for(const value of [process.role==='rag'?'Servidor RAG':'Ollama',`${process.name} · PID ${process.pid}`,percent(process.cpu_percent),`${number(process.ram_bytes/1024**3,2)} GiB`])row.append(node('td','',value));$('hardware-processes').append(row);}
      $('hardware-status').textContent=this.paused?'Visualización pausada · recogiendo lecturas':'Lectura real · '+new Date(data.time).toLocaleTimeString('es-ES')+' · cada 2 s';
      this.display={data,history:this.history.slice()};this.draw(this.display);
  }
  draw({data,history}){
    const end=Date.parse(data.time);const gpu=data.gpu.devices?.[0];
    const ramMax=Math.max(data.ram_total_bytes||0,data.history_memory_totals?.ram||0,...history.map(point=>point.ram_total_bytes||0))/1024**3;
    const vramMax=Math.max(gpu?.memory_total_bytes||0,data.history_memory_totals?.vram||0,...history.map(point=>point.vram_total_bytes||0))/1024**3;
    for(const chart of this.charts){const max=chart.key==='ram'?ramMax:chart.key==='vram'?vramMax:100;chart.draw(history,end,this.window,max,(data.history_gap_seconds||10)*1000,data.history_summary?.[chart.key]);}
    const visible=history.filter(point=>point.epoch>=end-this.window*1000);
    $('hardware-history-note').textContent=`${visible.length} puntos de ${data.history_sample_count??visible.length} lecturas registradas. Histórico local de 7 días; los huecos indican ausencia de lecturas. En ventanas largas se muestran menos puntos y las estadísticas usan todas las lecturas guardadas.`;
  }
}
