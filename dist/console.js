import {t, intlLocale} from './i18n.js';
import {$, node, copyText} from './ui.js';

export class ServerConsole {
  constructor(api) {
    this.api=api;this.lines=[];this.cursor=0;this.instance=null;this.active=false;this.paused=false;this.loading=false;this.timer=null;
    $('console-pause').addEventListener('click',()=>{this.paused=!this.paused;$('console-pause').textContent=this.paused?t("ui.resume_5685e1"):t("ui.pause_cd25ee");this.schedule(0);});
    $('console-clear').addEventListener('click',()=>{this.lines=[];this.render();});
    $('console-search').addEventListener('input',()=>this.render());
    $('console-level').addEventListener('change',()=>this.render());
    $('console-copy').addEventListener('click',()=>copyText(this.text(),t("ui.log_copied_3eec47")));
    $('console-download').addEventListener('click',()=>{const blob=new Blob([this.text()],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob);const link=node('a');link.href=url;link.download='rag-servidor.log';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
  }
  visibleLines(){const query=$('console-search').value.toLocaleLowerCase();const level=$('console-level').value;return this.lines.filter(line=>(!level||line.level===level)&&(!query||`${line.text} ${line.stream}`.toLocaleLowerCase().includes(query)));}
  text(){return this.visibleLines().map(line=>`${line.time} [${line.stream}/${line.level}] ${line.text}`).join('\n');}
  render(){
    const container=$('console-output');const previous=container.scrollTop;const follow=$('console-follow').checked;
    const lines=this.visibleLines();container.replaceChildren();
    for(const line of lines){const row=node('div',`console-line console-${line.level.toLowerCase()}`);const time=node('time','',(line.time_source==='read'?'≈ ':'')+new Date(line.time).toLocaleTimeString(intlLocale()));time.dateTime=line.time;time.title=line.time_source==='read'?t("ui.log_read_time_the_message_may_be_older_7a7845"):t("ui.message_received_time_a36d27");row.append(time,node('span','console-origin',`${line.stream} · ${line.level}:`),node('span','console-message',line.text));container.append(row);}
    $('console-empty').hidden=lines.length>0;$('console-count').textContent=t("ui.visible_lines_latest_1000_from_the_server_6ad840", {value1: (lines.length)});
    $('console-time-note').hidden=!lines.some(line=>line.time_source==='read');
    container.scrollTop=follow?container.scrollHeight:previous;
  }
  setActive(active){this.active=active;this.schedule(0);}
  schedule(delay=1000){clearTimeout(this.timer);this.timer=null;if(this.active&&!document.hidden){this.timer=setTimeout(()=>this.poll(),delay);}}
  async poll(){
    if(this.loading||!this.active)return;
    if(this.paused){$('console-status').textContent=t("ui.display_paused_8f866f");return;}
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
      if(this.instance&&this.instance!==result.instance){this.lines=[];this.cursor=0;this.instance=result.instance;$('console-status').textContent=t("ui.log_reconnected_retrieving_lines_dd9d11");this.render();return;}
      this.instance=result.instance;this.cursor=result.cursor;this.lines.push(...result.entries);this.lines=this.lines.slice(-1000);
      $('console-status').textContent=result.dropped?t("ui.live_log_truncated_to_the_memory_limit_a54a4e"):t("ui.live_updated_970018")+new Date().toLocaleTimeString(intlLocale());
      this.render();
    }catch(error){$('console-status').textContent=error.status===404?t("ui.the_console_will_be_available_after_restarting_the_server_with_th_b3c22b"):t("ui.waiting_for_the_server_connection_878ba6");}
    finally{this.loading=false;this.schedule();}
  }
}
