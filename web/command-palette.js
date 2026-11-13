import {$,node,button,normalize} from './ui.js';
import {t} from './i18n.js';
const text=(key)=>t(`header.${key}`);
export class CommandPalette{
  constructor(status){
    this.status=status;this.dialog=$('command-palette');
    $('command-open').addEventListener('click',()=>this.open());$('command-close').addEventListener('click',()=>this.dialog.close());
    $('command-query').addEventListener('input',()=>this.render());$('command-form').addEventListener('submit',event=>{event.preventDefault();if($('command-query').value.trim())this.ask();});
    document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();this.open();}});
    this.dialog.addEventListener('close',()=>{if(this.navigating){this.navigating=false;return;}(this.opener?.isConnected?this.opener:$('command-open')).focus({preventScroll:true});});
    this.dialog.addEventListener('click',event=>{const rect=this.dialog.getBoundingClientRect();if(event.target===this.dialog&&(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom))this.dialog.close();});
    this.dialog.addEventListener('keydown',event=>{if(!['ArrowDown','ArrowUp'].includes(event.key))return;const items=[...this.dialog.querySelectorAll('input,button,a')],index=items.indexOf(document.activeElement);event.preventDefault();items[(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();});
  }
  open(){if(this.dialog.open)return;this.opener=document.activeElement;$('system-popover').hidePopover();this.render();this.dialog.showModal();$('command-query').focus();}
  go(path){this.navigating=true;this.dialog.close();window.dispatchEvent(new CustomEvent('rag:navigate',{detail:path}));}
  ask(){const question=$('command-query').value.trim();this.go('/');$('question').value=question;$('question').dispatchEvent(new Event('input'));$('question').focus();}
  render(){
    const results=$('command-results'),query=$('command-query').value.trim();results.replaceChildren();const group=caption=>{const section=node('section'),title=node('h2','label',caption);section.append(title);results.append(section);return section;};
    if(query)group(text('ask')).append(button(query,'command-result',()=>this.ask()));
    const documents=group(text('documents')),matches=(this.status()?.documents||[]).filter(item=>normalize(item.name).includes(normalize(query))).slice(0,10);
    for(const item of matches){const link=node('a','command-result',item.name);link.href='/api/source?file='+encodeURIComponent(item.name);link.target='_blank';link.rel='noopener';link.addEventListener('click',()=>this.dialog.close());documents.append(link);}if(!matches.length)documents.append(node('p','meta',text('no_documents')));
    const navigation=group(text('go'));for(const [route,key]of[['/','chat'],['/admin','library'],['/configuracion','settings'],['/estadisticas','statistics'],['/consola','console']]){const caption=text(key);if(!query||normalize(caption).includes(normalize(query)))navigation.append(button(caption,'command-result',()=>this.go(route)));}
    if(this.status()?.features?.laboratory){const caption=t('lab.title');if(!query||normalize(caption).includes(normalize(query)))navigation.append(button(caption,'command-result',()=>this.go('/laboratorio')));}
  }
}
