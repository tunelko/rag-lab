import {$,node,copyText,icon} from './ui.js';
import {t,intlLocale} from './i18n.js';
const s=(key,values={})=>t(`sentinel.${key}`,values);
export class ServerConsole{
  constructor(api){
    this.api=api;this.lines=[];this.cursor=0;this.instance=null;this.active=false;this.paused=false;this.loading=false;this.timer=null;
    $('console-pause').addEventListener('click',()=>{this.paused=!this.paused;this.render();this.schedule(0);});
    $('console-clear').addEventListener('click',()=>{this.lines=[];this.render();});
    $('console-search').addEventListener('input',()=>this.render());$('console-level').addEventListener('change',()=>this.render());$('console-stream').addEventListener('change',()=>this.render());
    $('console-level-chips').addEventListener('click',event=>{const control=event.target.closest('[data-level]');if(control){$('console-level').value=control.dataset.level;this.render();}});
    $('console-copy').addEventListener('click',()=>copyText(this.text(),s('log_copied')));
    $('console-download').addEventListener('click',()=>{const blob=new Blob([this.text()],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download='rag-servidor.log';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    document.addEventListener('keydown',event=>{if(this.active&&event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.target.closest('input,textarea,select,[contenteditable]')){event.preventDefault();$('console-search').focus();}});
    document.addEventListener('visibilitychange',()=>this.schedule(0));
  }
  matching(){const query=$('console-search').value.toLocaleLowerCase(intlLocale()),stream=$('console-stream').value;return this.lines.filter(line=>(!stream||line.stream===stream)&&(!query||`${line.text} ${line.stream}`.toLocaleLowerCase(intlLocale()).includes(query)));}
  visibleLines(){const level=$('console-level').value;return this.matching().filter(line=>!level||line.level===level||(level==='WARNING'&&line.level==='WARN'));}
  text(){return this.visibleLines().map(line=>`${line.time} [${line.stream}/${line.level}] ${line.text}`).join('\n');}
  render(){
    const origin=$('console-stream'),selected=origin.value,streams=[...new Set(this.lines.map(line=>line.stream))].sort(),signature=JSON.stringify([streams,s('all_origins')]);
    if(origin.dataset.options!==signature){origin.dataset.options=signature;origin.replaceChildren();const all=node('option','',s('all_origins'));all.value='';origin.append(all);for(const stream of streams){const option=node('option','',stream);option.value=stream;origin.append(option);}origin.value=streams.includes(selected)?selected:'';}
    const matched=this.matching();for(const control of document.querySelectorAll('#console-level-chips [data-level]')){const level=control.dataset.level;control.setAttribute('aria-pressed',String($('console-level').value===level));control.querySelector('[data-level-count]').textContent=String(matched.filter(line=>!level||line.level===level||level==='WARNING'&&line.level==='WARN').length);}
    const container=$('console-output'),previous=container.scrollTop,follow=$('console-follow').checked,lines=this.visibleLines();container.setAttribute('aria-busy','true');container.replaceChildren();
    for(const line of lines){const row=node('div','log__line console-line console-'+line.level.toLowerCase()),time=node('time','log__time',(line.time_source==='read'?'≈ ':'')+new Date(line.time).toLocaleTimeString(intlLocale()));time.dateTime=line.time;time.title=s(line.time_source==='read'?'log_read_time':'log_received_time');
      const level=line.level==='WARNING'?'WARN':line.level;row.append(node('span','log__no',line.id),time,node('span','log__src '+(line.stream==='server'?'log__src--server':''),line.stream),node('span','log__lv log__lv--'+level,level),node('span','log__message console-message',line.text));container.append(row);}
    container.setAttribute('aria-busy','false');$('console-empty').hidden=lines.length>0;$('console-count').textContent=s('console_lines',{count:lines.length});$('console-time-note').hidden=!lines.some(line=>line.time_source==='read');container.scrollTop=follow?container.scrollHeight:previous;
    const pause=$('console-pause');pause.replaceChildren(icon(this.paused?'arrow':'clock'),node('span','sr-only',s(this.paused?'resume':'pause')));pause.setAttribute('aria-label',s(this.paused?'resume':'pause'));pause.title=s(this.paused?'resume':'pause');
    for(const [id,key]of[['console-clear','clear_display'],['console-copy','copy'],['console-download','download']]){$(id).title=s(key);$(id).setAttribute('aria-label',s(key));}
    if(this.paused)$('console-status').textContent=s('console_paused');
  }
  setActive(active){this.active=active;this.schedule(0);}
  schedule(delay=1000){clearTimeout(this.timer);this.timer=null;if(this.active&&!document.hidden)this.timer=setTimeout(()=>this.poll(),delay);}
  async poll(){
    if(this.loading||!this.active)return;if(this.paused){$('console-status').textContent=s('console_paused');return;}this.loading=true;
    try{let result;try{result=await this.api(`/api/console?after=${this.cursor}&limit=200`);}catch(error){if(error.status!==404)throw error;result=await this.api('/assets/runtime-console.json');result.entries=result.entries.filter(line=>line.id>this.cursor).slice(0,200);result.cursor=result.entries.at(-1)?.id||Math.min(this.cursor,result.newest);}
      if(this.instance&&this.instance!==result.instance){this.lines=[];this.cursor=0;this.instance=result.instance;this.render();return;}
      this.instance=result.instance;this.cursor=result.cursor;this.lines.push(...result.entries);this.lines=this.lines.slice(-1000);$('console-status').textContent=s('live',{time:new Date().toLocaleTimeString(intlLocale())});this.render();
    }catch{$('console-status').textContent=s('waiting_connection');}finally{this.loading=false;this.schedule();}
  }
}
