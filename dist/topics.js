import {$, node, button, number, toast} from './ui.js';
import {t, serverText} from './i18n.js';

const key='rag-local-source-topic',text=(name,values={})=>t(`library.${name}`,values);

export class LibraryTopics {
  constructor(api,refresh,filterChanged) {
    Object.assign(this,{api,refresh,filterChanged,filter:'',selected:new Set(),visible:[],topics:[],editId:null});
    try{$('chat-scope').dataset.choice=localStorage.getItem(key)||'';}catch{}
    $('new-topic').addEventListener('click',()=>this.edit());
    $('cancel-topic').addEventListener('click',()=>this.hideEditor());
    $('topic-form').addEventListener('submit',event=>{event.preventDefault();this.save();});
    $('move-documents').addEventListener('click',()=>this.move());
    $('select-visible-documents').addEventListener('change',event=>{for(const name of this.visible)event.target.checked?this.selected.add(name):this.selected.delete(name);this.filterChanged();});
    $('chat-scope').addEventListener('change',()=>{const value=$('chat-scope').value;$('chat-scope').dataset.choice=value;try{localStorage.setItem(key,value);}catch{}window.dispatchEvent(new CustomEvent('rag:scope'));});
    window.addEventListener('rag:languagechange',()=>{this.signature=null;this.render();});
  }
  setStatus(status) {
    this.documents=status.documents||[];this.topics=status.library?.topics||[];this.unassigned=status.library?.unassigned_count??this.documents.filter(item=>!item.topic_id).length;
    const names=new Set(this.documents.map(item=>item.name));for(const name of this.selected)if(!names.has(name))this.selected.delete(name);this.render();
  }
  path(topic){const parent=this.topics.find(item=>item.id===topic.parent_id);return parent?`${parent.name} / ${topic.name}`:topic.name;}
  choices(){return this.topics.filter(item=>!item.parent_id).flatMap(parent=>[parent,...this.topics.filter(item=>item.parent_id===parent.id)]);}
  fillSelect(id,entries,desired) {
    const select=$(id),value=desired??select.value,signature=JSON.stringify([entries,value]);if(select.dataset.options===signature)return;select.dataset.options=signature;select.replaceChildren();
    for(const [id,label] of entries){const option=node('option','',label);option.value=id;select.append(option);}
    if(value&&!entries.some(([id])=>id===value)){const option=node('option','',text('missing_scope'));option.value=value;option.disabled=true;select.append(option);}select.value=value;
  }
  render() {
    const destinations=[['',text('unassigned')],...this.choices().map(item=>[item.id,this.path(item)])];for(const id of ['move-topic','upload-topic'])this.fillSelect(id,destinations);
    this.fillSelect('chat-scope',[['',text('all')],['unassigned',text('unassigned')],...this.choices().map(item=>[item.id,this.path(item)+' · '+number(item.document_count)])],$('chat-scope').dataset.choice||'');
    if(!$('topic-form').hidden&&!this.editId)this.fillSelect('topic-parent',[['',text('root')],...this.topics.filter(item=>!item.parent_id).map(item=>[item.id,item.name])]);
    const signature=JSON.stringify([this.topics,this.unassigned,this.filter,text('all')]);if(this.signature===signature){this.selection();return;}this.signature=signature;
    const filters=$('topic-filters');filters.replaceChildren();
    for(const [id,label] of [['',text('all')],['unassigned',text('unassigned')+' · '+number(this.unassigned)]]){const control=button(label,'btn btn--secondary',()=>this.selectFilter(id));control.dataset.topic=id;control.setAttribute('aria-pressed',String(this.filter===id));filters.append(control);}
    const tree=$('topic-tree');tree.replaceChildren();
    for(const parent of this.topics.filter(item=>!item.parent_id)){
      const cabinet=node('section','topic-cabinet'),heading=node('div','topic-heading');heading.append(this.topicButton(parent));
      const add=button('+ '+text('drawer'),'btn btn--ghost',()=>this.edit(null,parent.id));add.title=text('new_drawer');heading.append(add);cabinet.append(heading,this.actions(parent));
      const drawers=node('div','topic-drawers');for(const child of this.topics.filter(item=>item.parent_id===parent.id)){const row=node('div','topic-drawer');row.append(this.topicButton(child),this.actions(child));drawers.append(row);}cabinet.append(drawers);tree.append(cabinet);
    }
    $('topic-empty').hidden=Boolean(this.topics.length);this.selection();
  }
  topicButton(topic){const control=button(topic.name,'btn btn--secondary topic-name',()=>this.selectFilter(topic.id));control.dataset.topic=topic.id;control.setAttribute('aria-pressed',String(this.filter===topic.id));control.append(node('span','num',number(topic.document_count)));control.title=text('scope_info',{count:topic.document_count,chunks:number(topic.chunk_count)});return control;}
  actions(topic){const actions=node('div','topic-actions'),rename=button(text('rename'),'btn btn--ghost',()=>this.edit(topic)),remove=button(text('remove_empty'),'btn btn--ghost',()=>this.remove(topic));remove.disabled=topic.document_count>0||this.topics.some(item=>item.parent_id===topic.id);actions.append(rename,remove);return actions;}
  selectFilter(id){this.filter=id;this.selected.clear();this.render();this.filterChanged();}
  includes(document){if(!this.filter)return true;if(this.filter==='unassigned')return !document.topic_id;return document.topic_id===this.filter||this.topics.some(item=>item.id===document.topic_id&&item.parent_id===this.filter);}
  checkbox(name){const input=node('input','document-select');input.type='checkbox';input.checked=this.selected.has(name);input.setAttribute('aria-label',text('select_document',{name}));input.addEventListener('change',()=>{input.checked?this.selected.add(name):this.selected.delete(name);this.selection();});return input;}
  selection(visible=this.visible){this.visible=visible;$('selected-documents').textContent=text('selected',{count:this.selected.size});$('move-documents').disabled=!this.selected.size;const count=visible.filter(name=>this.selected.has(name)).length,all=$('select-visible-documents');all.checked=Boolean(visible.length)&&count===visible.length;all.indeterminate=count>0&&count<visible.length;all.disabled=!visible.length;}
  edit(topic=null,parent=''){this.editId=topic?.id||null;$('topic-form').hidden=false;$('topic-message').hidden=true;$('topic-name').value=topic?.name||'';this.fillSelect('topic-parent',[['',text('root')],...this.topics.filter(item=>!item.parent_id).map(item=>[item.id,item.name])],topic?.parent_id||parent);$('topic-parent').disabled=Boolean(topic);$('topic-name').focus();}
  hideEditor(){this.editId=null;$('topic-form').hidden=true;$('topic-message').hidden=true;$('new-topic').focus();}
  error(error){$('topic-message').hidden=false;$('topic-message').textContent=serverText(error.message);}
  async save(){
    $('save-topic').disabled=true;try{await this.api('/api/admin/topics'+(this.editId?'/'+this.editId:''),{method:this.editId?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('topic-name').value.trim(),...(!this.editId?{parent_id:$('topic-parent').value||null}:{})})});this.hideEditor();await this.refresh();}catch(error){this.error(error);}finally{$('save-topic').disabled=false;}
  }
  async remove(topic){try{await this.api('/api/admin/topics/'+topic.id,{method:'DELETE'});if(this.filter===topic.id)this.filter='';await this.refresh();}catch(error){this.error(error);}}
  async move(){const names=[...this.selected];if(!names.length)return;$('move-documents').disabled=true;try{await this.api('/api/admin/library/assign',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({names,topic_id:$('move-topic').value||null})});this.selected.clear();await this.refresh();toast(text('moved',{count:names.length}));}catch(error){this.error(error);}finally{this.selection();}}
}
