import {$, node, number} from './ui.js';

const percent=value=>value==null?'—':`${number(value,1)} %`;
const memory=(used,total)=>used==null||total==null?'—':`${number(used/1024**3,1)} / ${number(total/1024**3,1)} GiB`;
export class Telemetry {
  constructor(api){this.api=api;this.active=false;this.loading=false;this.timer=null;document.addEventListener('visibilitychange',()=>this.schedule(0));}
  setActive(active){this.active=active;this.schedule(0);}
  schedule(delay=2000){clearTimeout(this.timer);if(this.active&&!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){
    if(this.loading||!this.active)return;this.loading=true;
    try{
      let data;try{data=await this.api('/api/telemetry');}catch(error){if(error.status!==404)throw error;data=(await this.api('/assets/runtime-console.json')).telemetry;}
      if(!data)throw new Error('Preparando lecturas');
      $('hardware-cpu').textContent=percent(data.cpu_percent);$('hardware-cpu-caption').textContent=`Uso total del equipo · ${data.logical_cpus} procesadores lógicos`;
      $('hardware-ram').textContent=memory(data.ram_used_bytes,data.ram_total_bytes);$('hardware-ram-caption').textContent=percent(data.ram_percent)+' de la RAM física';
      const gpu=data.gpu.devices?.[0];$('hardware-gpu').textContent=percent(gpu?.utilization_percent);$('hardware-gpu-caption').textContent=gpu?.name||data.gpu.error||'GPU no disponible';
      $('hardware-vram').textContent=gpu?memory(gpu.memory_used_bytes,gpu.memory_total_bytes):'—';
      $('hardware-vram-caption').textContent=gpu?[gpu.temperature_c==null?'':`${number(gpu.temperature_c,1)} °C`,gpu.power_w==null?'':`${number(gpu.power_w,1)} W`].filter(Boolean).join(' · '):'Sin lectura de memoria GPU';
      $('hardware-processes').replaceChildren();
      for(const process of data.processes){const row=node('tr');for(const value of [process.role==='rag'?'Servidor RAG':'Ollama',`${process.name} · PID ${process.pid}`,percent(process.cpu_percent),`${number(process.ram_bytes/1024**3,2)} GiB`])row.append(node('td','',value));$('hardware-processes').append(row);}
      $('hardware-status').textContent='Lectura real · '+new Date(data.time).toLocaleTimeString('es-ES')+' · cada 2 s';
    }catch(error){$('hardware-status').textContent='Esperando lecturas de hardware…';}
    finally{this.loading=false;this.schedule();}
  }
}
