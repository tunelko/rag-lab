import {$,node,number} from './ui.js';
import {t} from './i18n.js';

export class DocumentViewer {
  constructor(api) {
    this.api=api;this.dialog=$('document-viewer');this.content=$('document-content');this.sequence=0;
    window.addEventListener('rag:document',event=>this.open(event.detail));
    $('document-close').addEventListener('click',()=>this.dialog.close());
    $('document-maximize').addEventListener('click',()=>{
      const expanded=this.dialog.classList.toggle('is-fullscreen');
      $('document-maximize').setAttribute('aria-pressed',String(expanded));this.labels();
    });
    $('document-previous').addEventListener('click',()=>this.load(this.page-1));
    $('document-next').addEventListener('click',()=>this.load(this.page+1));
    $('document-pagination').addEventListener('submit',event=>{
      event.preventDefault();if($('document-page').checkValidity())this.load(Number($('document-page').value));
    });
    this.dialog.addEventListener('click',event=>{
      if(event.target!==this.dialog)return;
      const rect=this.dialog.getBoundingClientRect();
      if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)this.dialog.close();
    });
    this.dialog.addEventListener('close',()=>{
      this.sequence++;this.request?.abort();this.content.replaceChildren();this.dialog.classList.remove('is-fullscreen');
      $('document-maximize').setAttribute('aria-pressed','false');document.body.classList.remove('document-viewer-open');
      const target=this.returnFocus?.isConnected&&this.returnFocus.getClientRects().length?this.returnFocus:$('open-context');
      target.focus({preventScroll:true});
    });
    window.addEventListener('rag:languagechange',()=>this.labels());
  }
  labels() {
    if(this.file)$('document-title').textContent=this.file;
    const expanded=this.dialog.classList.contains('is-fullscreen'),control=$('document-maximize');
    const label=t(expanded?'viewer.restore':'viewer.maximize');control.title=label;control.setAttribute('aria-label',label);
    control.querySelector('use').setAttribute('href',expanded?'#i-restore':'#i-maximize');
    if(this.metadata?.kind==='pdf')$('document-location').textContent=t('viewer.location',{page:number(this.page),pages:number(this.metadata.pages)})+
      (this.metadata.page_label&&this.metadata.page_label!==String(this.page)?' · '+t('ui.document_numbering_9d763c',{value1:this.metadata.page_label}):'');
  }
  open(details) {
    const url=new URL(details.url,location.origin);
    if(url.origin!==location.origin||!(url.pathname==='/api/source'||/^\/api\/figures\/[a-f0-9]{64}$/.test(url.pathname)))return;
    this.source=url;this.file=details.file;this.metadata=null;this.page=Number(details.page)||1;
    if(!this.dialog.open){
      this.returnFocus=details.trigger||document.activeElement;document.body.classList.add('document-viewer-open');
      this.dialog.showModal();$('document-close').focus({preventScroll:true});
    }
    $('document-title').textContent=this.file;$('document-location').textContent='';this.labels();this.load(this.page);
  }
  controls(loading=false) {
    const pdf=this.metadata?.kind==='pdf';$('document-pagination').hidden=!pdf;
    $('document-previous').disabled=loading||this.page<=1;
    $('document-next').disabled=loading||this.page>=(this.metadata?.pages||1);
    $('document-page').disabled=loading;$('document-go').disabled=loading;
    if(pdf){$('document-page').max=this.metadata.pages;$('document-page').value=this.page;$('document-pages').textContent='/ '+number(this.metadata.pages);}
  }
  async load(page) {
    const sequence=++this.sequence;this.request?.abort();this.request=new AbortController();
    this.content.replaceChildren();$('document-status').hidden=false;$('document-status').textContent=t('viewer.loading');
    this.dialog.setAttribute('aria-busy','true');this.controls(true);
    try{
      const figure=this.source.pathname.startsWith('/api/figures/');
      const metadata=figure?{kind:'image',url:this.source.pathname}:await this.api('/api/source/preview?'+new URLSearchParams({file:this.source.searchParams.get('file'),page}),{signal:this.request.signal});
      if(sequence!==this.sequence||!this.dialog.open)return;
      this.metadata=metadata;this.page=metadata.page||1;this.labels();
      if(metadata.kind==='text'){
        this.content.append(node('pre','document-text',metadata.text));
        if(metadata.truncated)this.content.prepend(node('p','banner banner--warn',t('viewer.truncated')));
        this.ready(sequence);return;
      }
      const image=node('img','document-image');image.alt=this.metadata.kind==='pdf'?t('viewer.page_image',{file:this.file,page:number(this.page)}):this.file;
      image.onload=()=>this.ready(sequence);image.onerror=()=>this.failed(sequence);
      image.src=metadata.url;this.content.append(image);this.content.scrollTop=0;
    }catch(error){if(sequence===this.sequence&&this.dialog.open)this.failed(sequence);}
  }
  ready(sequence){if(sequence!==this.sequence||!this.dialog.open)return;$('document-status').hidden=true;this.dialog.setAttribute('aria-busy','false');this.controls();}
  failed(sequence){if(sequence!==this.sequence||!this.dialog.open)return;this.content.replaceChildren();$('document-status').hidden=false;$('document-status').textContent=t('viewer.error');this.dialog.setAttribute('aria-busy','false');this.controls();}
}
