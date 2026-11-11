import {$, node, button, icon, number, toast} from './ui.js';
import {t, serverText} from './i18n.js';

const key='rag-local-source-topic',text=(name,values={})=>t(`library.${name}`,values);

export class LibraryTopics {
  constructor(api,refresh,filterChanged) {
    Object.assign(this,{api,refresh,filterChanged,filter:'',selected:new Set(),visible:[],topics:[],editId:null,collapsed:new Set()});
    try{$('chat-scope').dataset.choice=localStorage.getItem(key)||'';}catch{}
    $('new-topic').addEventListener('click',()=>this.edit());
    $('category-search').addEventListener('input',()=>{this.signature=null;this.render();});
    $('upload-topic').addEventListener('change',()=>this.destination());
    document.addEventListener('click',event=>{for(const menu of document.querySelectorAll('.category-menu[open]'))if(!menu.contains(event.target))menu.open=false;});
    document.addEventListener('keydown',event=>{if(event.key==='Escape')for(const menu of document.querySelectorAll('.category-menu[open]')){menu.open=false;menu.querySelector('summary').focus();}});
    $('cancel-topic').addEventListener('click',()=>this.hideEditor());
    $('topic-form').addEventListener('submit',event=>{event.preventDefault();this.save();});
    $('move-documents').addEventListener('click',()=>this.move());
    $('select-visible-documents').addEventListener('change',event=>{for(const name of this.visible)event.target.checked?this.selected.add(name):this.selected.delete(name);this.filterChanged();});
    $('chat-scope').addEventListener('change',()=>{const value=$('chat-scope').value;$('chat-scope').dataset.choice=value;try{localStorage.setItem(key,value);}catch{}window.dispatchEvent(new CustomEvent('rag:scope'));});
    window.addEventListener('rag:languagechange',()=>{this.signature=null;this.render();});
  }
  setStatus(status) {
    this.documents=status.documents||[];this.topics=status.library?.topics||[];this.records=status.records||{};this.unassigned=status.library?.unassigned_count??this.documents.filter(item=>!item.topic_id).length;
    const names=new Set(this.documents.map(item=>item.name));for(const name of this.selected)if(!names.has(name))this.selected.delete(name);this.render();
  }
  systemTopics(){const counts=this.records?.counts||{},count=Number(counts.vulnerabilities||counts.kev||0);return[
    {id:'records',parent_id:null,label_key:'cybersecurity',document_count:count,chunk_count:0,virtual:true},
    {id:'records:vulnerabilities',parent_id:'records',label_key:'vulnerabilities',document_count:count,chunk_count:0,virtual:true},
  ];}
  displayedTopics(){return [...this.topics,...this.systemTopics()];}
  label(topic){return topic?.label_key?text(topic.label_key):topic?.name||'';}
  path(topic,topics=this.displayedTopics()){const parent=topics.find(item=>item.id===topic?.parent_id),label=this.label(topic);return parent?`${this.label(parent)} / ${label}`:label;}
  choices(topics=this.topics){return topics.filter(item=>!item.parent_id).flatMap(parent=>[parent,...topics.filter(item=>item.parent_id===parent.id)]);}
  fillSelect(id,entries,desired) {
    const select=$(id),value=desired??select.value,signature=JSON.stringify([entries,value]);if(select.dataset.options===signature)return;select.dataset.options=signature;select.replaceChildren();
    for(const [id,label] of entries){const option=node('option','',label);option.value=id;select.append(option);}
    if(value&&!entries.some(([id])=>id===value)){const option=node('option','',text('missing_scope'));option.value=value;option.disabled=true;select.append(option);}select.value=value;
  }
  render() {
    const destinations=[['',text('unassigned')],...this.choices().map(item=>[item.id,this.path(item)])];for(const id of ['move-topic','upload-topic'])this.fillSelect(id,destinations);
    const displayed=this.displayedTopics();
    this.fillSelect('chat-scope',[['',text('all')],['unassigned',text('unassigned')],...this.choices(displayed).map(item=>[item.id,this.path(item,displayed)+' · '+number(item.document_count)])],$('chat-scope').dataset.choice||'');
    if(!$('topic-form').hidden&&!this.editId)this.fillSelect('topic-parent',[['',text('root')],...this.topics.filter(item=>!item.parent_id).map(item=>[item.id,item.name])]);
    this.destination();
    const signature=JSON.stringify([displayed,this.unassigned,this.filter,text('all'),text('cybersecurity'),text('vulnerabilities'),$('category-search').value,[...this.collapsed]]);if(this.signature===signature){this.selection();return;}this.signature=signature;
    const filters=$('topic-filters');filters.replaceChildren();
    for(const [id,label,count] of [['',text('all'),this.documents?.length||0],['unassigned',text('unassigned'),this.unassigned]]){const control=button('','category-choice',()=>this.selectFilter(id));control.dataset.topic=id;control.setAttribute('aria-pressed',String(this.filter===id));control.append(icon(id?'file':'library'),node('span','category-label',label),node('span','category-count',number(count)));filters.append(control);}
    const tree=$('topic-tree');tree.replaceChildren();
    const search=$('category-search').value.toLocaleLowerCase().trim();
    for(const parent of displayed.filter(item=>!item.parent_id)){
      const allChildren=displayed.filter(item=>item.parent_id===parent.id),parentLabel=this.label(parent),parentMatches=parentLabel.toLocaleLowerCase().includes(search),children=allChildren.filter(item=>parentMatches||this.label(item).toLocaleLowerCase().includes(search));
      if(search&&!parentMatches&&!children.length)continue;
      const branch=node('section','category-branch'),heading=node('div','category-row');heading.append(this.topicButton(parent),this.actions(parent));branch.append(heading);
      if(allChildren.length){const expanded=Boolean(search)||!this.collapsed.has(parent.id),toggle=button('','category-expand',()=>{expanded?this.collapsed.add(parent.id):this.collapsed.delete(parent.id);this.signature=null;this.render();});toggle.append(icon('chevron'));toggle.setAttribute('aria-label',text(expanded?'collapse':'expand',{name:parentLabel}));toggle.setAttribute('aria-expanded',String(expanded));toggle.setAttribute('aria-controls','category-children-'+parent.id);heading.prepend(toggle);const list=node('div','category-children');list.id='category-children-'+parent.id;list.hidden=!expanded;for(const child of children){const row=node('div','category-row');row.append(this.topicButton(child),this.actions(child));list.append(row);}branch.append(list);}
      tree.append(branch);
    }
    $('topic-empty').hidden=Boolean(tree.children.length);$('topic-empty').textContent=text(search?'no_categories':'empty');this.selection();
  }
  topicButton(topic){const control=button('','category-choice',()=>this.selectFilter(topic.id));control.dataset.topic=topic.id;control.setAttribute('aria-pressed',String(this.filter===topic.id));control.append(icon(topic.virtual?'lock':'folder'),node('span','category-label',this.label(topic)),node('span','category-count',number(topic.document_count)));control.title=topic.virtual?text('record_scope_info',{count:number(topic.document_count)}):text('scope_info',{count:topic.document_count,chunks:number(topic.chunk_count)});return control;}
  actions(topic){if(topic.virtual){const marker=node('span','category-system');marker.title=text('structured_source');marker.setAttribute('aria-label',text('structured_source'));marker.append(icon('lock'));return marker;}const menu=node('details','category-menu'),summary=node('summary'),items=node('div','category-menu-items');summary.setAttribute('aria-label',text('category_actions',{name:topic.name}));summary.append(icon('more'));menu.append(summary,items);const action=(caption,callback)=>{const control=button(caption,'category-action',()=>{menu.open=false;callback();});items.append(control);return control;};if(!topic.parent_id)action(text('new_subcategory'),()=>{this.collapsed.delete(topic.id);this.edit(null,topic.id);});action(text('rename'),()=>this.edit(topic));const remove=action(text('remove_empty'),()=>this.remove(topic));remove.disabled=topic.document_count>0||this.topics.some(item=>item.parent_id===topic.id);if(remove.disabled)remove.title=text('empty_only');return menu;}
  selectFilter(id){this.filter=id;this.selected.clear();if(!id.startsWith('records')){$('upload-topic').value=id==='unassigned'?'':id;$('upload-topic').dispatchEvent(new Event('change'));}this.render();this.filterChanged();}
  destination(){const topic=this.topics.find(item=>item.id===$('upload-topic').value),label=topic?this.path(topic):text('unassigned'),displayed=this.displayedTopics();$('upload-destination-hint').textContent=text(topic?'destination_hint':'uncategorized_hint',{name:label});$('library-location').textContent=this.filter==='unassigned'?text('unassigned'):this.path(displayed.find(item=>item.id===this.filter)||{name:text('all')},displayed);}
  includes(document){if(!this.filter)return true;if(this.filter==='unassigned')return !document.topic_id;if(this.filter==='records'||this.filter.startsWith('records:'))return false;return document.topic_id===this.filter||this.topics.some(item=>item.id===document.topic_id&&item.parent_id===this.filter);}
  checkbox(name){const input=node('input','document-select');input.type='checkbox';input.checked=this.selected.has(name);input.setAttribute('aria-label',text('select_document',{name}));input.addEventListener('change',()=>{input.checked?this.selected.add(name):this.selected.delete(name);this.selection();});return input;}
  selection(visible=this.visible){this.visible=visible;$('selected-documents').textContent=text('selected',{count:this.selected.size});$('move-documents').disabled=!this.selected.size;const count=visible.filter(name=>this.selected.has(name)).length,all=$('select-visible-documents');all.checked=Boolean(visible.length)&&count===visible.length;all.indeterminate=count>0&&count<visible.length;all.disabled=!visible.length;}
  edit(topic=null,parent=''){this.editId=topic?.id||null;for(const menu of document.querySelectorAll('.category-menu[open]'))menu.open=false;$('topic-form').hidden=false;$('topic-message').hidden=true;$('topic-editor-title').textContent=text(topic?'rename':parent?'new_subcategory':'new_category');$('topic-name').value=topic?.name||'';this.fillSelect('topic-parent',[['',text('root')],...this.topics.filter(item=>!item.parent_id).map(item=>[item.id,item.name])],topic?.parent_id||parent);$('topic-parent').disabled=Boolean(topic);$('topic-name').focus();}
  hideEditor(){this.editId=null;$('topic-form').hidden=true;$('topic-message').hidden=true;$('new-topic').focus();}
  error(error){$('topic-message').hidden=false;$('topic-message').textContent=serverText(error.message);}
  async save(){
    $('save-topic').disabled=true;try{await this.api('/api/admin/topics'+(this.editId?'/'+this.editId:''),{method:this.editId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('topic-name').value.trim(),...(!this.editId?{parent_id:$('topic-parent').value||null}:{})})});this.hideEditor();await this.refresh();}catch(error){this.error(error);}finally{$('save-topic').disabled=false;}
  }
  async remove(topic){try{await this.api('/api/admin/topics/'+topic.id,{method:'DELETE'});if(this.filter===topic.id)this.filter='';await this.refresh();}catch(error){this.error(error);}}
  async move(){const names=[...this.selected];if(!names.length)return;$('move-documents').disabled=true;try{await this.api('/api/admin/library/assign',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names,topic_id:$('move-topic').value||null})});this.selected.clear();await this.refresh();toast(text('moved',{count:names.length}));}catch(error){this.error(error);}finally{this.selection();}}
}
