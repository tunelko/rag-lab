export function toPoint(data){
  const gpu=data.gpu?.devices?.[0]||{};
  const stored=data.history?.find(point=>point.time===data.time)||{};
  return {...stored,break_before:Boolean(data.current_break_before||stored.break_before),time:data.time,cpu_percent:data.cpu_percent,gpu_percent:gpu.utilization_percent,
    ram_used_bytes:data.ram_used_bytes,ram_total_bytes:data.ram_total_bytes,
    vram_used_bytes:gpu.memory_used_bytes,vram_total_bytes:gpu.memory_total_bytes};
}
export function mergeHistory(previous,data){
  const end=Date.parse(data.time);if(!Number.isFinite(end))return previous;
  const seconds=data.history_seconds||900;
  const points=new Map();
  for(const point of [...(seconds>900?[]:previous),...(data.history||[]),toPoint(data)]){
    const time=Date.parse(point.time);
    if(Number.isFinite(time)&&time>=end-seconds*1000&&time<=end)points.set(time,{...point,epoch:time});
  }
  return [...points.values()].sort((a,b)=>a.epoch-b.epoch).slice(seconds>900?0:-512);
}
export function metricValue(point,key){
  const value={cpu:point.cpu_percent,gpu:point.gpu_percent,ram:point.ram_used_bytes,vram:point.vram_used_bytes}[key];
  if(value==null||!Number.isFinite(value))return null;
  return key==='ram'||key==='vram'?value/1024**3:value;
}
export function chartSeries(history,key,start,end,max,gap=10000){
  const segments=[];let segment=[];let previous=null;const values=[];
  for(const point of history){
    if(point.epoch<start||point.epoch>end)continue;
    const value=metricValue(point,key);
    if(value==null||max<=0){if(segment.length)segments.push(segment);segment=[];previous=null;continue;}
    if(point.break_before||point.break_before_keys?.includes(key)||(previous!=null&&point.epoch-previous>gap)){if(segment.length)segments.push(segment);segment=[];}
    const sample={...point,value,x:12+(point.epoch-start)/(end-start)*584,y:168-Math.max(0,Math.min(max,value))/max*158};
    segment.push(sample);values.push(sample);previous=point.epoch;
  }
  if(segment.length)segments.push(segment);
  return {segments,values,min:values.length?Math.min(...values.map(point=>point.value)):null,
    max:values.length?Math.max(...values.map(point=>point.value)):null,
    mean:values.length?values.reduce((sum,point)=>sum+point.value,0)/values.length:null};
}
