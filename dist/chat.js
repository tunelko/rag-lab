import {randomId} from './ids.js';
import {$,node,button,iconButton,icon,renderText,renderSources,copyText,toast,announce,number} from './ui.js';
import {t,getLanguage,serverText} from './i18n.js';
import {createMusa} from './musa.js';
import {ContextPanel} from './context-panel.js';

const STORE='rag-local-chat-v3',s=(key,values={})=>t(`sentinel.${key}`,values);
const busyStatus=status=>['queued','running'].includes(status);

export class Chat{
  constructor(api,changed,beforeSend=async()=>{}){
    this.api=api;this.changed=changed;this.beforeSend=beforeSend;this.turns=[];this.active=null;this.submitting=false;this.stopping=false;this.contextTurnId=null;this.attachments=[];this.turnImages=new Map();this.elements=new Map();
    try{this.turns=JSON.parse(sessionStorage.getItem(STORE)||'[]').slice(-30);}catch{sessionStorage.removeItem(STORE);}
    this.contextPanel=new ContextPanel();this.render();
    $('question').addEventListener('input',()=>this.controls());
    $('question').addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&!$('ask-button').disabled){event.preventDefault();$('query-form').requestSubmit();}});
    $('query-form').addEventListener('submit',event=>{event.preventDefault();this.send();});
    $('new-chat').addEventListener('click',()=>{if(this.active||this.submitting)return;this.turns=[];this.turnImages.clear();this.contextTurnId=null;this.save();this.render();$('question').focus();});
    $('attach-image').addEventListener('click',()=>$('query-image-input').click());
    $('query-image-input').addEventListener('change',()=>this.addImages([...$('query-image-input').files]));
    window.addEventListener('rag:source',event=>{const turn=this.turns.find(turn=>`turn-${turn.id}`===event.detail.prefix);if(turn)this.selectContext(turn,event.detail.number);});
    window.addEventListener('rag:scope',()=>this.suggestions());
  }
  save(){this.turns=this.turns.slice(-30);try{sessionStorage.setItem(STORE,JSON.stringify(this.turns));}catch{toast(s('history_full'));}}
  controls(){
    const busy=Boolean(this.active||this.submitting);$('ask-button').querySelector('span').textContent=s(busy?this.stopping?'stopping':'stop':'send');
    $('ask-button').disabled=this.submitting||this.stopping||(!busy&&(!this.status||!$('question').value.trim()));$('ask-button').setAttribute('aria-busy',String(busy));$('new-chat').disabled=busy;$('question').required=!busy;
    $('attach-image').disabled=busy;$('attach-image').title=s('attach_image');$('attach-image').setAttribute('aria-label',s('attach_image'));$('question-help').textContent='Ctrl + Enter';$('conversation-count').textContent=s('question_count',{count:this.turns.length});
  }
  setStatus(status){
    this.status=status;this.controls();this.suggestions();this.context();
    const empty=$('chat-empty').querySelector('.musa');empty.dataset.state=status.ollama.connected?'idle':'offline';empty.classList.toggle('is-static',Boolean(this.turns.length));
    let banner=$('ollama-offline');if(!banner){banner=node('div','banner banner--err');banner.id='ollama-offline';$('query-view').querySelector('.page-head').after(banner);}banner.hidden=status.ollama.connected;banner.textContent=s('ollama_start');
  }
  suggestions(){
    const container=$('chat-suggestions');container.replaceChildren();
    const topicId=$('chat-scope').value,topics=this.status?.library?.topics||[];
    for(const document of (this.status?.documents||[]).filter(item=>item.indexed&&(!topicId||(topicId==='unassigned'?!item.topic_id:item.topic_id===topicId||topics.some(topic=>topic.id===item.topic_id&&topic.parent_id===topicId)))).slice(0,3)){
      const question=s('suggest_document',{name:document.name});const control=button(question,'btn btn--secondary',()=>{$('question').value=question;this.controls();$('question').focus();});container.append(control);
    }
  }
  async addImages(files){
    for(const file of files){
      if(this.attachments.length>=4){toast(s('image_count_limit'));break;}
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>3*1024**2){toast(s('image_limits'));continue;}
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(file);});
      this.attachments.push({name:file.name,data});
    }
    $('query-image-input').value='';this.renderAttachments();
  }
  renderAttachments(){
    const container=$('query-attachments');container.replaceChildren();container.hidden=!this.attachments.length;
    this.attachments.forEach((image,index)=>{const item=node('div','query-attachment'),preview=node('img');preview.src=image.data;preview.alt='';const remove=iconButton('x',s('remove_image',{name:image.name}),()=>{this.attachments.splice(index,1);this.renderAttachments();});item.append(preview,node('span','',image.name),remove);container.append(item);});
  }
  create(turn){
    const article=node('article','chat-turn');article.dataset.turn=turn.id;article.append(node('div','msg-user chat-question',turn.question));
    article.append(node('p','turn-scope',turn.topic_id?(turn.scope_label==='unassigned'?t('library.unassigned'):turn.scope_label||t('library.missing_scope')):t('library.all')));
    if(turn.attachment_names?.length)article.append(node('p','user-attachments',turn.attachment_names.join(' · ')));
    const reply=node('div','msg-ai chat-reply'),avatar=createMusa(),body=node('div','reply-body'),answer=node('div','answer'),state=node('p','chat-state'),steps=node('ol','chat-step-chips');state.setAttribute('role','status');steps.setAttribute('aria-label',s('query_progress'));
    const chips=node('div','source-chips'),trace=node('div','msg-trace'),facts=node('span','trace-facts'),actions=node('div','trace-actions chat-actions');
    const copy=button(s('copy'),'btn btn--ghost',()=>copyText(turn.answer||''),s('copy_answer')),retry=button(s('retry'),'btn btn--secondary',()=>this.send(turn.question,turn.id)),view=node('a','trace-link',s('view_trace'));actions.append(copy,retry,view);trace.append(facts,actions);body.append(state,steps,answer,chips,trace);reply.append(avatar,body);article.append(reply);$('chat-thread').append(article);
    const result={article,avatar,answer,state,steps,chips,facts,copy,retry,view,sourceKey:''};this.elements.set(turn.id,result);return result;
  }
  update(turn){
    const thread=$('chat-thread'),atBottom=thread.scrollHeight-thread.scrollTop-thread.clientHeight<80,e=this.elements.get(turn.id)||this.create(turn),sources=turn.sources||[],metrics=turn.metrics||{},busy=busyStatus(turn.status);
    renderText(e.answer,turn.answer||'',new Set(sources.map(source=>Number(source.citation))),`turn-${turn.id}`);
    e.answer.querySelector('.stream-caret')?.remove();if(busy&&turn.answer){const caret=node('span','stream-caret');caret.setAttribute('aria-hidden','true');e.answer.append(caret);}
    const avatar=turn.status==='error'?'offline':busy?turn.answer?'answering':sources.length?'thinking':'retrieving':'idle';e.avatar.dataset.state=avatar;e.avatar.classList.toggle('is-static',!busy||avatar==='offline');
    const indexing=this.status?.jobs?.some(job=>job.kind==='index'&&job.status==='running');
    e.state.textContent=turn.status==='error'?serverText(turn.error_info?.message)||s('query_error'):turn.status==='cancelled'?s('query_stopped'):busy?turn.status==='queued'?s(indexing?'waiting_index':'queued'):turn.answer?s('writing'):sources.length?s('thinking_context',{count:sources.length,images:metrics.images_used??'—'}):serverText(turn.phase)||s('retrieving_state'):'';
    e.state.hidden=!e.state.textContent;e.state.classList.toggle('is-thinking',avatar==='thinking');
    if(avatar==='thinking'){const dots=node('span','thinking-dots');dots.setAttribute('aria-hidden','true');for(let i=0;i<3;i++)dots.append(node('i'));e.state.append(dots);}
    e.steps.replaceChildren();e.steps.hidden=!busy;
    if(busy){
      const ready=sources.length>0||metrics.vector_search_seconds!=null,hasRoute=Boolean(turn.model||metrics.model);
      const stages=[{caption:s('vectorized'),done:ready,run:!ready},{caption:s('sources_ready',{count:sources.length||'—'}),done:ready,run:false}];
      if(metrics.images_used!=null)stages.push({caption:s('images_ready',{count:metrics.images_used}),done:hasRoute,run:false});
      stages.push({caption:hasRoute?s('generating_with',{model:turn.model||metrics.model}):s('preparing_model'),done:false,run:ready});
      for(const stage of stages){const chip=node('li','step-chip'+(stage.done?' is-done':stage.run?' is-run':''));if(stage.done)chip.append(icon('check'));chip.append(document.createTextNode(stage.caption));e.steps.append(chip);}
    }
    const key=getLanguage()+JSON.stringify(sources);if(e.sourceKey!==key){e.chips.replaceChildren();for(const source of sources){const link=node('a','source-chip');link.href=`#turn-${turn.id}-${source.citation}`;link.title=source.file;link.append(node('span','',`[${source.citation}]`),node('span','source-chip__name',source.file+(source.page?' · '+s('page_short')+' '+source.page:'')),node('span','num',number(source.relevance??Math.max(0,1-Number(source.cosine_distance||0)),2)));link.addEventListener('click',event=>{event.preventDefault();this.selectContext(turn,source.citation);});e.chips.append(link);}e.sourceKey=key;}
    e.facts.replaceChildren();const facts=[turn.model||metrics.model];if(metrics.routing_mode)facts.push(s(metrics.routing_mode==='automatic'?metrics.visual_mode==='images'?'auto_visual':'auto_text':metrics.routing_mode==='search_only'?'retrieval_only':metrics.routing_mode==='catalog'?'catalog':'manual'));if(metrics.images_used!=null)facts.push(s('image_count',{count:metrics.images_used}));if(turn.elapsed_seconds!=null)facts.push(number(turn.elapsed_seconds,1)+' s');if(metrics.generation_tok_s!=null)facts.push(number(metrics.generation_tok_s,2)+' tok/s');if(metrics.output_tokens!=null)facts.push(number(metrics.output_tokens)+' tok');e.facts.textContent=facts.filter(Boolean).join(' · ');
    e.copy.disabled=busy||!turn.answer;e.retry.hidden=!['error','cancelled'].includes(turn.status);e.retry.disabled=Boolean(this.active||this.submitting);e.view.hidden=!turn.job_id;e.view.href='/estadisticas?query='+encodeURIComponent(turn.job_id||'');e.article.setAttribute('aria-busy',String(busy));
    if(this.contextTurnId===turn.id)this.context();if(atBottom)thread.scrollTop=thread.scrollHeight;
  }
  selectContext(turn,citation){this.contextTurnId=turn.id;this.context();this.contextPanel.open(citation);}
  context(){
    const turn=this.turns.find(turn=>turn.id===this.contextTurnId),sources=turn?.sources||[];
    const signature=JSON.stringify([getLanguage(),turn?.id,turn?.question,sources]);
    if(signature!==this.contextSignature){this.contextSignature=signature;renderSources(sources,turn?.question||'',$('sources'),`turn-${turn?.id||'empty'}`);$('context-count').textContent=number(sources.length);$('context-empty').hidden=Boolean(sources.length);}
    const reason=serverText(turn?.metrics?.routing_reason)||s('route_empty');if($('context-route').textContent!==reason)$('context-route').textContent=reason;
    const model=this.status?.embedding_model?.split('/').at(-1),dimensions=this.status?.embedding_dimensions,info=model?model+' · '+s('dimension_count',{count:dimensions==null?'—':number(dimensions)}):'—';if($('query-vector-info').textContent!==info)$('query-vector-info').textContent=info;
  }
  render(){
    $('chat-thread').replaceChildren();this.elements.clear();if(!this.turns.some(turn=>turn.id===this.contextTurnId))this.contextTurnId=this.turns.at(-1)?.id||null;
    for(const turn of this.turns)this.update(turn);$('chat-empty').hidden=Boolean(this.turns.length);this.context();this.controls();this.renderAttachments();
  }
  history(before,topicId){return this.turns.slice(0,before).filter(turn=>turn.status==='done'&&(turn.topic_id||null)===(topicId||null)).slice(-3).flatMap(turn=>[{role:'user',content:turn.question.slice(0,3000)},{role:'assistant',content:(turn.answer||'').slice(0,3000)}]);}
  async send(question=$('question').value.trim(),retryId=null){
    if(this.active){this.stopping=true;this.controls();try{await this.api(`/api/jobs/${this.active}`,{method:'DELETE'});}catch(error){toast(error.message);this.stopping=false;this.controls();}return;}
    if(this.submitting||!question)return;
    const scope=$('chat-scope');if(scope.selectedOptions[0]?.disabled){toast(t('library.missing_scope'));return;}
    const previous=retryId?this.turns.find(turn=>turn.id===retryId):null,images=this.attachments.length?this.attachments:(retryId?this.turnImages.get(retryId)||[]:[]);
    if(previous?.attachment_names?.length&&!images.length){toast(s('reattach_image'));return;}
    this.submitting=true;this.controls();try{await this.beforeSend();}catch(error){this.submitting=false;this.controls();toast(error.message);return;}
    const topicId=previous?previous.topic_id:scope.value,turn=previous||{id:randomId(),question,topic_id:topicId||null,scope_label:topicId==='unassigned'?'unassigned':scope.selectedOptions[0]?.textContent.replace(/ · [\d.,]+$/,'')};if(!previous)this.turns.push(turn);this.contextTurnId=turn.id;
    Object.assign(turn,{answer:'',sources:[],metrics:{},status:'queued',job_id:null,attachment_names:images.map(image=>image.name)});this.turnImages.set(turn.id,images);this.render();
    try{const result=await this.api('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,topic_id:turn.topic_id||null,language:getLanguage(),history:this.history(this.turns.indexOf(turn),turn.topic_id),images:images.map(image=>image.data)})});turn.job_id=result.job_id;$('question').value='';this.attachments=[];this.renderAttachments();this.save();this.submitting=false;await this.follow(turn);}
    catch(error){this.submitting=false;turn.status='error';turn.error_info={message:error.message};this.update(turn);this.save();this.controls();}
  }
  async follow(turn){
    if(this.active)return;this.active=turn.job_id;this.stopping=false;this.controls();
    try{while(true){const job=await this.api(`/api/jobs/${turn.job_id}`);window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));Object.assign(turn,{status:job.status,phase:job.phase,answer:job.answer||'',sources:job.sources||[],error_info:job.error_info,metrics:job.metrics||{},model:job.model,elapsed_seconds:job.elapsed_seconds});this.update(turn);
      if(['done','error','cancelled'].includes(job.status)){this.save();announce(s(job.status==='done'?'answer_ready':'query_stopped'),'query-announcements');this.changed();break;}await new Promise(resolve=>setTimeout(resolve,700));}}
    catch(error){turn.status='error';turn.error_info={message:error.message};this.update(turn);this.save();}
    finally{this.active=null;this.submitting=false;this.stopping=false;this.controls();for(const item of this.turns)this.update(item);}
  }
  restore(jobs){const job=jobs.find(item=>item.kind==='query');if(job&&!this.active){let turn=this.turns.find(item=>item.job_id===job.id);if(!turn){turn={id:randomId(),question:job.question||'',job_id:job.id,status:job.status};this.turns.push(turn);this.contextTurnId=turn.id;this.render();}this.follow(turn);}else if(!job&&!this.active){for(const turn of this.turns.filter(item=>busyStatus(item.status))){turn.status='cancelled';this.update(turn);}this.save();}}
}
