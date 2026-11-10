import {t} from './i18n.js';
import {Marked} from './vendor/marked/marked.esm.js';
import DOMPurify from './vendor/dompurify/purify.es.js';

const escape=text=>String(text).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const parser=new Marked({gfm:true,breaks:false,async:false});
parser.use({renderer:{
  html(token){return escape(token.text);},
  image(token){return escape(t("ui.image_17593b", {value1: (token.text)}));},
  heading(token){const level=Math.min(6,token.depth+1);return `<h${level}>${this.parser.parseInline(token.tokens)}</h${level}>\n`;},
}});
const cache=new WeakMap();

function citation(number,prefix){
  const link=document.createElement('a');link.className='citation';link.textContent=String(number);link.href=`#${prefix}-${number}`;
  link.setAttribute('aria-label',t("ui.view_source_7ad4a8", {value1: (number)}));
  link.addEventListener('click',event=>{event.preventDefault();window.dispatchEvent(new CustomEvent('rag:source',{detail:{prefix,number}}));});
  return link;
}

function addCitations(fragment,citations,prefix){
  // Numeric reference-style Markdown links also resolve to the actual RAG source.
  for(const link of fragment.querySelectorAll('a')){
    const match=link.textContent.match(/^\[?(\d+)\]?$/);
    if(match&&citations.has(Number(match[1])))link.replaceWith(citation(Number(match[1]),prefix));
  }
  const walker=document.createTreeWalker(fragment,NodeFilter.SHOW_TEXT);
  const nodes=[];let current;
  while((current=walker.nextNode()))if(!current.parentElement?.closest('pre,code,a,button'))nodes.push(current);
  for(const text of nodes){
    const replacement=document.createDocumentFragment();let previous=0;let changed=false;
    for(const match of text.textContent.matchAll(/\[(\d+)\]/g)){
      if(!citations.has(Number(match[1])))continue;
      replacement.append(document.createTextNode(text.textContent.slice(previous,match.index)),citation(Number(match[1]),prefix));
      previous=match.index+match[0].length;changed=true;
    }
    if(changed){replacement.append(document.createTextNode(text.textContent.slice(previous)));text.replaceWith(replacement);}
  }
}

function normalizeLinks(fragment){
  for(const link of fragment.querySelectorAll('a')){
    const raw=link.getAttribute('href');if(!raw)continue;
    try{
      const url=new URL(raw,location.href);
      if(!['http:','https:','mailto:'].includes(url.protocol)){link.removeAttribute('href');continue;}
      if(url.origin!==location.origin){link.target='_blank';link.rel='noopener noreferrer';}
    }catch{link.removeAttribute('href');}
  }
}

function decorateCode(fragment){
  for(const pre of fragment.querySelectorAll('pre')){
    const code=pre.querySelector('code');if(!code)continue;
    const language=code.className.match(/(?:^|\s)language-([\w+-]+)/)?.[1]||'texto';
    code.className=language==='texto'?'':`language-${language}`;
    const section=document.createElement('div');section.className='markdown-code';
    const header=document.createElement('div');header.className='markdown-code-heading';
    const label=document.createElement('span');label.textContent=language;
    const copy=document.createElement('button');copy.type='button';copy.textContent=t("ui.copy_39dcf1");copy.className='text-button';copy.setAttribute('aria-label',t("ui.copy_code_block_91ce14"));
    copy.addEventListener('click',async()=>{
      try{await navigator.clipboard.writeText(code.textContent);copy.textContent=t("ui.copied_d8dd87");}
      catch{copy.textContent=t("ui.could_not_copy_2af300");}
      setTimeout(()=>{copy.textContent=t("ui.copy_39dcf1");},2000);
    });
    header.append(label,copy);pre.replaceWith(section);section.append(header,pre);
  }
}

export function renderMarkdown(container,text,citations=new Set(),prefix='src'){
  const raw=String(text);const key=JSON.stringify([raw,[...citations].sort(),prefix]);
  if(cache.get(container)===key)return;
  container.classList.add('markdown');
  try{
    const fragment=DOMPurify.sanitize(parser.parse(raw),{
      RETURN_DOM_FRAGMENT:true,SANITIZE_NAMED_PROPS:true,ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false,
      ALLOWED_TAGS:['p','br','h1','h2','h3','h4','h5','h6','strong','em','del','s','pre','code','blockquote','ul','ol','li','hr','table','thead','tbody','tfoot','tr','th','td','a','input'],
      ALLOWED_ATTR:['href','title','class','start','align','colspan','rowspan','type','checked','disabled'],
      FORBID_ATTR:['style','id','name'],
    });
    for(const input of fragment.querySelectorAll('input')){
      if(input.getAttribute('type')!=='checkbox'){input.remove();continue;}
      input.disabled=true;input.tabIndex=-1;input.closest('li')?.classList.add('markdown-task');
    }
    normalizeLinks(fragment);addCitations(fragment,citations,prefix);decorateCode(fragment);
    for(const table of fragment.querySelectorAll('table')){
      const wrapper=document.createElement('div');wrapper.className='markdown-table';wrapper.tabIndex=0;wrapper.setAttribute('role','region');wrapper.setAttribute('aria-label',t("ui.answer_table_081c1c"));
      table.replaceWith(wrapper);wrapper.append(table);
    }
    container.replaceChildren(fragment);
  }catch{
    // Keep incomplete or unexpected streamed content readable without HTML insertion.
    container.textContent=raw;
  }
  cache.set(container,key);
}
