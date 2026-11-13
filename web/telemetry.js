import {t, intlLocale} from './i18n.js';
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
    $('hardware-pause').addEventListener('click',()=>{this.paused=!this.paused;$('hardware-pause').textContent=this.paused?t("ui.resume_5685e1"):t("ui.pause_charts_a84524");$('hardware-pause').setAttribute('aria-pressed',String(this.paused));if(this.paused)$('hardware-status').textContent=t("ui.display_paused_collecting_readings_d8948d");else if(this.latest)this.render(this.latest);});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
  }
  setActive(active){this.active=active;this.schedule(0);}
  schedule(delay=2000){clearTimeout(this.timer);if(this.active&&!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){
    if(this.loading||!this.active)return;this.loading=true;
    const requestedWindow=this.window;
    try{
      let data;try{data=await this.api(`/api/telemetry?history_seconds=${requestedWindow}`);}catch(error){if(error.status!==404)throw error;data=(await this.api('/assets/runtime-console.json')).telemetry;}
      if(!data)throw new Error(t("ui.preparing_readings_36b52b"));
      if(requestedWindow!==this.window)return;
      this.history=mergeHistory(this.history,data);this.latest=data;window.dispatchEvent(new CustomEvent("rag:telemetry",{detail:data}));
      if(!this.paused||this.rangeChanged)this.render(data);this.rangeChanged=false;
    }catch(error){if(!this.paused)$('hardware-status').textContent=t("ui.waiting_for_hardware_readings_40063e");}
    finally{this.loading=false;this.schedule(requestedWindow!==this.window?0:2000);}
  }
  render(data){
      $('hardware-cpu').textContent=percent(data.cpu_percent);$('hardware-cpu-caption').textContent=t("ui.system_utilization_logical_processors_354e4d", {value1: (data.logical_cpus)});
      $('hardware-ram').textContent=memory(data.ram_used_bytes,data.ram_total_bytes);$('hardware-ram-caption').textContent=percent(data.ram_percent)+t("ui.of_physical_ram_1338ea");
      const gpu=data.gpu.devices?.[0];$('hardware-gpu').textContent=percent(gpu?.utilization_percent);$('hardware-gpu-caption').textContent=gpu?.name||data.gpu.error||t("ui.gpu_unavailable_2f8a32");
      $('hardware-gpu-sensors').textContent=gpu?[gpu.temperature_c==null?'':t("ui.core_c_c20a90", {value1: (number(gpu.temperature_c,1))}),gpu.power_w==null?'':`${number(gpu.power_w,1)} W`].filter(Boolean).join(' · '):'';
      $('hardware-vram').textContent=gpu?memory(gpu.memory_used_bytes,gpu.memory_total_bytes):'—';
      $('hardware-vram-caption').textContent=gpu?.memory_total_bytes?percent(100*gpu.memory_used_bytes/gpu.memory_total_bytes)+t("ui.of_dedicated_memory_db06c0"):t("ui.no_gpu_memory_reading_e00a2e");
      $('hardware-processes').replaceChildren();
      for(const process of data.processes){const row=node('tr');for(const value of [process.role==='rag'?t("ui.rag_server_1f279b"):'Ollama',`${process.name} · PID ${process.pid}`,percent(process.cpu_percent),`${number(process.ram_bytes/1024**3,2)} GiB`])row.append(node('td','',value));$('hardware-processes').append(row);}
      $('hardware-status').textContent=this.paused?t("ui.display_paused_collecting_readings_d8948d"):t("ui.actual_reading_0b3bc9")+new Date(data.time).toLocaleTimeString(intlLocale())+t("ui.every_2_s_3417fe");
      this.display={data,history:this.history.slice()};this.draw(this.display);
  }
  draw({data,history}){
    const end=Date.parse(data.time);const gpu=data.gpu.devices?.[0];
    const ramMax=Math.max(data.ram_total_bytes||0,data.history_memory_totals?.ram||0,...history.map(point=>point.ram_total_bytes||0))/1024**3;
    const vramMax=Math.max(gpu?.memory_total_bytes||0,data.history_memory_totals?.vram||0,...history.map(point=>point.vram_total_bytes||0))/1024**3;
    for(const chart of this.charts){const max=chart.key==='ram'?ramMax:chart.key==='vram'?vramMax:100;chart.draw(history,end,this.window,max,(data.history_gap_seconds||10)*1000,data.history_summary?.[chart.key]);}
    const visible=history.filter(point=>point.epoch>=end-this.window*1000);
    $('hardware-history-note').textContent=t("ui.points_from_recorded_readings_seven_days_of_local_history_gaps_in_fbf5b9", {value1: (visible.length), value2: (data.history_sample_count??visible.length)});
  }
}
