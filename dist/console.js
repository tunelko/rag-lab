import {$, node, copyText} from './ui.js';

export class ServerConsole {
  constructor(api) {
    this.api=api;this.lines=[];this.cursor=0;this.instance=null;this.active=false;this.paused=false;this.loading=false;this.timer=null;
    $('console-pause').addEventListener('click',()=>{this.paused=!this.paused;$('console-pause').textContent=this.paused?'Reanudar':'Pausar';this.schedule(0);});
    $('console-clear').addEventListener('click',()=>{this.lines=[];this.render();});
    $('console-search').addEventListener('input',()=>this.render());
    $('console-level').addEventListener('change',()=>this.render());
    $('console-copy').addEventListener('click',()=>copyText(this.text(),'Registro copiado'));
    $('console-download').addEventListener('click',()=>{const blob=new Blob([this.text()],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob);const link=node('a');link.href=url;link.download='rag-servidor.log';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
  }
  visibleLines(){const query=$('console-search').value.toLocaleLowerCase();const level=$('console-level').value;return this.lines.filter(line=>(!level||line.level===level)&&(!query||`${line.text} ${line.stream}`.toLocaleLowerCase().includes(query)));}
  text(){return this.visibleLines().map(line=>`${line.time} [${line.stream}/${line.level}] ${line.text}`).join('\n');}
  render(){
    const container=$('console-output');const previous=container.scrollTop;const follow=$('console-follow').checked;
    const lines=this.visibleLines();container.replaceChildren();
    for(const line of lines){const row=node('div',`console-line console-${line.level.toLowerCase()}`);const time=node('time','',(line.time_source==='read'?'≈ ':'')+new Date(line.time).toLocaleTimeString('es-ES'));time.dateTime=line.time;time.title=line.time_source==='read'?'Hora de lectura del registro: el mensaje puede ser anterior.':'Hora de recepción del mensaje';row.append(time,node('span','console-origin',`${line.stream} · ${line.level}:`),node('span','console-message',line.text));container.append(row);}
    $('console-empty').hidden=lines.length>0;$('console-count').textContent=`${lines.length} líneas visibles · últimas 1000 del servidor`;
    $('console-time-note').hidden=!lines.some(line=>line.time_source==='read');
    container.scrollTop=follow?container.scrollHeight:previous;
  }
  setActive(active){this.active=active;this.schedule(0);}
  schedule(delay=1000){clearTimeout(this.timer);this.timer=null;if(this.active&&!document.hidden){this.timer=setTimeout(()=>this.poll(),delay);}}
  async poll(){
    if(this.loading||!this.active)return;
    if(this.paused){$('console-status').textContent='Visualización pausada';return;}
    this.loading=true;
    try{
      let result;
      try{result=await this.api(`/api/console?after=${this.cursor}&limit=200`);}
      catch(error){
        if(error.status!==404)throw error;
        result=await this.api('/assets/runtime-console.json');
        result.entries=result.entries.filter(line=>line.id>this.cursor).slice(0,200);
        result.cursor=result.entries.at(-1)?.id||Math.min(this.cursor,result.newest);
      }
      if(this.instance&&this.instance!==result.instance){this.lines=[];this.cursor=0;this.instance=result.instance;$('console-status').textContent='Registro reconectado · recuperando líneas…';this.render();return;}
      this.instance=result.instance;this.cursor=result.cursor;this.lines.push(...result.entries);this.lines=this.lines.slice(-1000);
      $('console-status').textContent=result.dropped?'En directo · el registro se ha recortado al límite de memoria':'En directo · actualizado '+new Date().toLocaleTimeString('es-ES');
      this.render();
    }catch(error){$('console-status').textContent=error.status===404?'La consola estará disponible al reiniciar el servidor con esta actualización.':'Esperando conexión con el servidor…';}
    finally{this.loading=false;this.schedule();}
  }
}
