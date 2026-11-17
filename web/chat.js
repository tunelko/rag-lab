import {randomId} from './ids.js';
import {$,node,iconButton,icon,renderText,renderSources,toast,announce,number} from './ui.js';
import {t,getLanguage,intlLocale,serverText} from './i18n.js';
import {createMusa} from './musa.js';
import {ContextPanel} from './context-panel.js';
import {sourceScope} from './source-scope.js';
import {groupSources,relevance} from './chat-sources.js';
import {sessionKey} from './collection-scope.js';
import {PipelineScene} from './pipeline-scene.js';
import {journey,scopeChunks} from './chat-journey.js';

const STORE=sessionKey('rag-local-chat-v3'),VISIBLE_DOCUMENTS=4,s=(key,values={})=>t(`sentinel.${key}`,values);
const busyStatus=status=>['queued','running'].includes(status);
const clock=value=>new Intl.DateTimeFormat(intlLocale(),{hour:'2-digit',minute:'2-digit'}).format(new Date(value));
const plural=(key,count)=>s(`${key}_${count===1?'one':'other'}`,{count:number(count)});
const j=(key,values={})=>t(`journey.${key}`,values),CHECKS=['vector','fragments','route','answer','cited'];
// Rewriting identical text would restart animations and drop a selection.
const put=(element,text)=>{if(element.textContent!==text)element.textContent=text;};
// A plain control with an optional icon; the shared .btn look does not apply inside an answer.
function control(tag,className,label,iconName){const element=node(tag,className);if(tag==='button')element.type='button';if(iconName)element.append(icon(iconName));element.append(node('span','',label));return element;}

export class Chat{
  constructor(api,changed,beforeSend=async()=>{},patchSettings=()=>{}){
    this.api=api;this.changed=changed;this.beforeSend=beforeSend;this.patchSettings=patchSettings;this.turns=[];this.active=null;this.submitting=false;this.stopping=false;this.contextTurnId=null;this.attachments=[];this.turnImages=new Map();this.elements=new Map();this.settings=null;
    try{this.turns=JSON.parse(sessionStorage.getItem(STORE)||'[]').slice(-30);}catch{sessionStorage.removeItem(STORE);}
    // Light theme side column: the scene of this question and its checklist, ticked as the job is polled.
    this.scene=new PipelineScene($('journey-scene'),{mini:true});this.checks=new Map();
    for(const key of CHECKS){const row=node('li'),box=node('span','check'),text=node('span','checks__text'),title=node('span','checks__title'),detail=node('span','checks__detail num'),state=node('span','sr-only');box.setAttribute('aria-hidden','true');box.append(icon('check'));text.append(title,detail);row.append(box,text,state);$('journey-checks').append(row);this.checks.set(key,{row,title,detail,state});}
    this.contextPanel=new ContextPanel();this.render();
    const question=$('question'),thread=$('chat-thread'),box=$('composer-box');
    question.addEventListener('input',()=>{this.autosize();this.controls();});
    question.addEventListener('keydown',event=>{
      if(event.key!=='Enter'||event.isComposing||event.keyCode===229)return;
      if(event.ctrlKey||event.metaKey){
        event.preventDefault();
        question.setRangeText('\n',question.selectionStart,question.selectionEnd,'end');
        question.dispatchEvent(new Event('input',{bubbles:true}));
        return;
      }
      if(event.shiftKey||event.altKey)return;
      event.preventDefault();
      if(!event.repeat&&!this.active&&!this.submitting&&!this.stopping&&!$('ask-button').disabled)$('query-form').requestSubmit();
    });
    $('query-form').addEventListener('submit',event=>{event.preventDefault();this.send();});
    $('new-chat').addEventListener('click',()=>{if(this.active||this.submitting)return;this.turns=[];this.turnImages.clear();this.contextTurnId=null;this.save();this.render();question.focus();});
    $('attach-image').addEventListener('click',()=>$('query-image-input').click());
    $('query-image-input').addEventListener('change',()=>this.addImages([...$('query-image-input').files]));
    $('chat-top-k').addEventListener('change',event=>this.patchSettings({k:Number(event.target.value)}));
    // Dropping an image on the composer attaches it to the question.
    let depth=0;const carriesFiles=event=>[...(event.dataTransfer?.types||[])].includes('Files');
    box.addEventListener('dragenter',event=>{if(!carriesFiles(event))return;event.preventDefault();depth++;box.classList.add('is-drop');});
    box.addEventListener('dragover',event=>{if(carriesFiles(event))event.preventDefault();});
    box.addEventListener('dragleave',()=>{depth=Math.max(0,depth-1);if(!depth)box.classList.remove('is-drop');});
    box.addEventListener('drop',event=>{if(!carriesFiles(event))return;event.preventDefault();depth=0;box.classList.remove('is-drop');if(!this.active&&!this.submitting)this.addImages([...event.dataTransfer.files]);});
    // Reading earlier text is never interrupted: the view only follows while it is at the end.
    thread.addEventListener('scroll',()=>this.jump(),{passive:true});
    $('chat-jump').addEventListener('click',()=>{thread.scrollTop=thread.scrollHeight;this.jump();});
    window.addEventListener('rag:source',event=>{const turn=this.turns.find(turn=>`turn-${turn.id}`===event.detail.prefix);if(turn)this.selectContext(turn,Number(event.detail.number),event.detail.trigger);});
    window.addEventListener('rag:scope',()=>{this.suggestions();this.scopeWarning();this.controls();});
    window.addEventListener('rag:languagechange',()=>{this.suggestions();this.scopeWarning();});
    $('scope-use-all').addEventListener('click',()=>{
      const scope=$('chat-scope'),previous=scope.value,last=this.turns.at(-1);
      if(!question.value.trim()&&last?.topic_id===previous&&last.metrics?.scope_documents===0)question.value=last.question;
      scope.value='';scope.dispatchEvent(new Event('change',{bubbles:true}));this.autosize();this.controls();question.focus();
    });
  }
  save(){this.turns=this.turns.slice(-30);try{sessionStorage.setItem(STORE,JSON.stringify(this.turns));}catch{toast(s('history_full'));}}
  autosize(){const input=$('question');input.style.height='auto';const height=input.scrollHeight;input.style.height=height?Math.min(height,240)+'px':'';}
  atEnd(){const thread=$('chat-thread');return thread.scrollHeight-thread.scrollTop-thread.clientHeight<80;}
  jump(){$('chat-jump').hidden=!this.turns.length||this.atEnd();}
  scopeName(){const scope=$('chat-scope');return scope.value?scope.selectedOptions[0]?.textContent.replace(/ · [\d.,]+$/,'')||'':'';}
  controls(){
    const busy=Boolean(this.active||this.submitting),ask=$('ask-button'),empty=!$('question').value.trim(),count=this.turns.length,first=this.turns[0]?.asked_at,name=this.scopeName();
    ask.querySelector('span').textContent=s(busy?this.stopping?'stopping':'stop':'send');ask.classList.toggle('send--stop',busy);ask.classList.toggle('send--go',!busy);
    ask.disabled=this.submitting||this.stopping||(!busy&&(!this.status||empty));ask.setAttribute('aria-busy',String(busy));
    $('ask-reason').textContent=!busy&&empty?s('send_needs_question'):'';
    $('new-chat').disabled=busy;$('question').required=!busy;$('question').placeholder=name?s('ask_scope',{name}):t('ui.ask_your_documents_45c5ff');
    $('attach-image').disabled=busy;$('attach-image').title=s('attach_image');$('attach-image').setAttribute('aria-label',s('attach_image'));
    $('conversation-count').textContent=count?[plural('question_count',count),first?s('started_at',{time:clock(first)}):''].filter(Boolean).join(' · '):'';
    put($('chat-crumb'),s('chat')+' / '+(name||t('library.all')));$('chat-pill-questions').hidden=!count;put($('chat-pill-questions'),count?plural('question_count',count):'');
    $('chat-pill-fragments').hidden=!this.status;put($('chat-pill-fragments'),this.status?plural('fragments',scopeChunks(this.status,$('chat-scope').value)):'');
  }
  // The composer shows the same top-k as Settings and writes it through the same store.
  setSettings(value){
    this.settings=value;if(!value)return;
    if($('chat-top-k').value!==String(value.k))$('chat-top-k').value=String(value.k);
  }
  setStatus(status){
    this.status=status;this.controls();this.suggestions();this.context();this.scopeWarning();
    const empty=$('chat-empty').querySelector('.musa');empty.dataset.state=status.ollama.connected?'idle':'offline';empty.classList.toggle('is-static',Boolean(this.turns.length));
    let banner=$('ollama-offline');if(!banner){banner=node('div','banner banner--err');banner.id='ollama-offline';$('query-view').querySelector('.page-head').after(banner);}banner.hidden=status.ollama.connected;banner.textContent=s('ollama_start');
  }
  scopeWarning(){
    const id=$('chat-scope').value,scope=sourceScope(this.status,id);
    const visible=Boolean(this.status&&id&&scope.exists&&!scope.indexed&&scope.elsewhere);
    $('scope-empty-banner').hidden=!visible;
    if(visible){
      const message=t('library.empty_scope',{name:this.scopeName()||t('library.missing_scope')});
      if($('scope-empty-message').textContent!==message)$('scope-empty-message').textContent=message;
    }
  }
  suggestions(){
    const container=$('chat-suggestions');container.replaceChildren();
    const topicId=$('chat-scope').value,topics=this.status?.library?.topics||[];
    const selected=topics.find(topic=>topic.id===topicId),parent=topics.find(topic=>topic.id===selected?.parent_id),scope=[parent?.name,selected?.name].filter(Boolean).join(' / ');
    const questions=selected
      ?[s('suggest_scope_summary',{name:scope}),s('suggest_scope_requirements',{name:scope}),s('suggest_scope_compare',{name:scope})]
      :[s('suggest_library_contents'),s('suggest_library_summary'),s('suggest_library_compare')];
    const title=node('p','suggestions__title',s('examples')),list=node('div','suggestions__list');
    for(const question of questions){const prompt=control('button','suggestion-prompt',question,'arrow');prompt.addEventListener('click',()=>{$('question').value=question;this.autosize();this.controls();$('question').focus();});list.append(prompt);}
    container.append(title,list);
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
    const article=node('article','chat-turn');article.dataset.turn=turn.id;article.setAttribute('aria-label',s('answer_of_musa'));
    const ask=node('div','q'),tag=node('span','q__meta'),meta=node('span');tag.append(icon('user'),meta);ask.append(node('div','q__bubble',turn.question));
    if(turn.attachment_names?.length)ask.append(node('p','q__files',turn.attachment_names.join(' · ')));ask.append(tag);
    const reply=node('div','a'),avatarBox=node('div','a__avatar'),avatar=createMusa(),main=node('div','a__main');avatarBox.append(avatar);
    const head=node('div','a__head'),route=node('span','a__route'),state=node('span','a__state chat-state'),model=node('span','a__model');state.setAttribute('role','status');head.append(node('strong','a__name','Musa'),route,state,model);
    const steps=node('ol','steps2');steps.setAttribute('aria-label',s('query_progress'));
    const answer=node('div','answer a__body'),sources=node('section','sources');sources.hidden=true;
    const actions=node('div','a__actions'),copy=control('button','act',s('copy'),'copy'),retry=control('button','act',s('regenerate'),'refresh'),view=control('a','act',s('view_trace'),'activity'),trace=node('span','a__trace');
    copy.setAttribute('aria-label',s('copy_answer'));copy.addEventListener('click',()=>this.copy(turn,copy));retry.addEventListener('click',()=>this.send(turn.question,turn.id));
    actions.append(copy,retry,view,trace);main.append(head,steps,answer,sources,actions);reply.append(avatarBox,main);article.append(ask,reply);$('chat-thread').append(article);
    const result={article,meta,avatar,route,state,model,steps,answer,sources,copy,retry,view,trace,sourceKey:''};this.elements.set(turn.id,result);return result;
  }
  async copy(turn,button){
    try{await navigator.clipboard.writeText(turn.answer||'');}catch{toast(t('ui.could_not_copy_select_the_text_and_use_ctrl_c_687bf3'));return;}
    const label=button.querySelector('span');label.textContent=s('copied');button.classList.add('is-done');announce(s('copied'),'query-announcements');
    clearTimeout(button.timer);button.timer=setTimeout(()=>{label.textContent=s('copy');button.classList.remove('is-done');},2000);
  }
  update(turn){
    const thread=$('chat-thread'),atEnd=this.atEnd(),e=this.elements.get(turn.id)||this.create(turn),sources=turn.sources||[],metrics=turn.metrics||{},busy=busyStatus(turn.status);
    const scope=turn.topic_id?(turn.scope_label==='unassigned'?t('library.unassigned'):turn.scope_label||t('library.missing_scope')):t('library.all');
    e.meta.textContent=[turn.asked_at?clock(turn.asked_at):'',s('asked_in',{scope})].filter(Boolean).join(' · ');
    renderText(e.answer,turn.answer||'',new Set(sources.map(source=>Number(source.citation))),`turn-${turn.id}`);
    e.answer.querySelector('.stream-caret')?.remove();if(busy&&turn.answer){const caret=node('span','stream-caret');caret.setAttribute('aria-hidden','true');e.answer.append(caret);}
    e.answer.hidden=!turn.answer;
    const avatar=turn.status==='error'?'offline':busy?turn.answer?'answering':sources.length?'thinking':'retrieving':'idle';e.avatar.dataset.state=avatar;e.avatar.classList.toggle('is-static',!busy||avatar==='offline');
    const indexing=(this.status?.global_jobs||this.status?.jobs)?.some(job=>job.kind==='index'&&job.status==='running');
    // One state chip: what is happening now, or how the answer ended.
    let label='',kind='';
    if(turn.status==='error'){label=serverText(turn.error_info?.message)||s('query_error');kind='error';}
    else if(turn.status==='cancelled'){label=s(turn.answer?'stopped_partial':'stopped');kind='stopped';}
    else if(busy){
      const doing=turn.status==='queued'?s(indexing?'waiting_index':'queued'):turn.answer?s('writing'):sources.length?s('thinking_context',{count:sources.length,images:metrics.images_used??'—'}):serverText(turn.phase)||s('retrieving_state');
      const seconds=turn.sent_at?Math.max(0,(Date.now()-turn.sent_at)/1000):null;
      label=doing.replace(/[.…]+$/,'')+(seconds==null?'':' · '+number(seconds,1)+' s');kind='run';
    }
    if(e.state.textContent!==label)e.state.textContent=label;e.state.hidden=!label;e.state.className='a__state chat-state'+(kind?' a__state--'+kind:'');
    const mode=busy?'':metrics.routing_mode||'',visual=metrics.visual_mode==='images';
    const routeKey=!mode?'':mode==='search_only'?'retrieval_only':mode==='catalog'?'catalog':visual?'route_visual':mode.startsWith('external')?'route_cloud':'route_text';
    e.route.textContent=routeKey?s(routeKey):'';e.route.hidden=!routeKey;e.route.classList.toggle('a__route--visual',routeKey==='route_visual');e.route.dataset.route=routeKey;
    const finished=busy?[]:[turn.model||metrics.model,turn.elapsed_seconds!=null&&turn.status!=='error'?number(turn.elapsed_seconds,1)+' s':''].filter(Boolean);
    e.model.textContent=finished.join(' · ');e.model.hidden=!finished.length;
    e.steps.replaceChildren();e.steps.hidden=!busy;
    if(busy){
      const ready=sources.length>0||metrics.vector_search_seconds!=null,hasRoute=Boolean(turn.model||metrics.model);
      const stages=[{caption:s('vectorized'),done:ready,run:!ready},{caption:s('sources_ready',{count:sources.length||'—'}),done:ready,run:false}];
      if(metrics.retrieval_mode==='structured')stages.splice(0,stages.length,{caption:t('collections.interpreting'),done:ready,run:!ready},{caption:t('collections.graph_lookup'),done:false,run:ready});
      if(metrics.images_used!=null)stages.push({caption:s('images_ready',{count:metrics.images_used}),done:hasRoute,run:false});
      if(metrics.retrieval_mode!=='structured')stages.push({caption:hasRoute?s('generating_with',{model:turn.model||metrics.model}):s('preparing_model'),done:false,run:ready});
      for(const stage of stages){const chip=node('li',stage.done?'is-done':stage.run?'is-run':'');if(stage.done)chip.append(icon('check'));chip.append(document.createTextNode(stage.caption));e.steps.append(chip);}
    }
    const key=getLanguage()+JSON.stringify(sources);if(e.sourceKey!==key){this.renderSourceList(turn,e.sources,sources);e.sourceKey=key;}
    const facts=[];if(metrics.routing_mode)facts.push(s(metrics.routing_mode==='automatic'?visual?'auto_visual':'auto_text':metrics.routing_mode==='search_only'?'retrieval_only':metrics.routing_mode==='catalog'?'catalog':metrics.routing_mode.startsWith('external')?'external':'manual'));
    if(metrics.images_used!=null)facts.push(s('image_count',{count:metrics.images_used}));if(metrics.output_tokens!=null)facts.push(number(metrics.output_tokens)+' tok');if(metrics.generation_tok_s!=null)facts.push(number(metrics.generation_tok_s,2)+' tok/s');
    e.trace.textContent=busy?'':facts.join(' · ');
    e.copy.hidden=busy||!turn.answer;e.retry.hidden=busy;e.retry.disabled=Boolean(this.active||this.submitting);e.retry.querySelector('span').textContent=s(turn.status==='error'?'retry':'regenerate');
    e.view.hidden=busy||!turn.job_id;e.view.href='/estadisticas?query='+encodeURIComponent(turn.job_id||'');e.article.setAttribute('aria-busy',String(busy));
    if(this.contextTurnId===turn.id)this.context();if(atEnd)thread.scrollTop=thread.scrollHeight;this.jump();
  }
  // One row per document: its citation numbers, a readable title and its best relevance.
  renderSourceList(turn,box,sources){
    box.replaceChildren();box.classList.remove('is-expanded');box.hidden=!sources.length;box.setAttribute('aria-label',s('answer_sources'));if(!sources.length)return;
    const groups=groupSources(sources),head=node('div','sources__head'),list=node('ol','sources__list');
    head.append(icon('layers'),node('h3','sources__title',s('sources_title')),node('span','sources__count',plural('documents',groups.length)+' · '+plural('fragments',sources.length)),node('span','sources__label',sources.every(source=>source.match_type==='documented_relationship')?t('collections.documented'):s('relevance_label')));
    for(const group of groups){
      const row=node('li','src-row'),numbers=node('span','src-row__nums'),text=node('span','src-row__text'),file=node('span','src-row__file');row.dataset.file=group.file;
      for(const citation of group.citations){const chip=node('button','src-num',citation);chip.type='button';chip.setAttribute('aria-label',s('show_in_context',{number:citation}));chip.addEventListener('click',()=>this.selectContext(turn,citation,chip));numbers.append(chip);}
      const best=group.fragments.reduce((top,item)=>relevance(item)>relevance(top)?item:top),title=node('button','src-row__title',group.title);title.type='button';title.title=group.file;
      title.addEventListener('click',()=>this.selectContext(turn,best.citation,title));
      file.append(node('span','src-row__name',group.file),node('span','',' · '+plural('fragments',group.fragments.length)));text.append(title,file);
      const score=node('span','src-row__rel'),bar=node('span','bar'),fill=node('i');fill.style.width=Math.round(group.relevance*100)+'%';bar.append(fill);
      score.title=t('ui.cosine_similarity_clipped_to_0_1_this_is_not_a_probability_ebe451');score.append(bar,node('span','src-row__score',number(group.relevance,2)));
      if(group.fragments.every(source=>source.match_type==='documented_relationship')){score.title=t('collections.documented');score.replaceChildren(node('span','',t('collections.documented')));}
      row.append(numbers,text,score);list.append(row);
    }
    box.append(head,list);
    if(groups.length>VISIBLE_DOCUMENTS){
      const more=node('div','sources__more'),toggle=node('button','act act--accent');toggle.type='button';
      const caption=()=>{const open=box.classList.contains('is-expanded');toggle.replaceChildren(node('span','',open?s('show_fewer'):s('show_more',{count:number(groups.length-VISIBLE_DOCUMENTS)})),icon('chevron'));toggle.setAttribute('aria-expanded',String(open));};
      toggle.addEventListener('click',()=>{box.classList.toggle('is-expanded');caption();});caption();more.append(toggle);box.append(more);
    }
  }
  // A citation or a document opens the Context panel on that fragment, lit against the darkened rest.
  selectContext(turn,citation,trigger){this.contextTurnId=turn.id;this.context();this.contextPanel.open(citation,trigger);}
  context(){
    const turn=this.turns.find(turn=>turn.id===this.contextTurnId),sources=turn?.sources||[];
    const signature=JSON.stringify([getLanguage(),turn?.id,turn?.question,sources]);
    if(signature!==this.contextSignature){this.contextSignature=signature;renderSources(sources,turn?.question||'',$('sources'),`turn-${turn?.id||'empty'}`);$('context-count').textContent=number(sources.length);$('context-empty').hidden=Boolean(sources.length);}
    const reason=serverText(turn?.metrics?.routing_reason)||s('route_empty');if($('context-route').textContent!==reason)$('context-route').textContent=reason;
    const model=this.status?.embedding_model?.split('/').at(-1),dimensions=this.status?.embedding_dimensions,info=model?model+' · '+s('dimension_count',{count:dimensions==null?'—':number(dimensions)}):'—';if($('query-vector-info').textContent!==info)$('query-vector-info').textContent=info;
    this.insight(turn);
  }
  // «Así viajó tu pregunta» and «En esta consulta»: the same turn the Context panel shows, read from its polled job.
  insight(turn){
    const view=journey(turn),f=view.facts,live=['queued','retrieving','generating'].includes(view.phase),count=number(f.fragments||0);
    this.scene.set(view.scene);put($('journey-step'),j('step',{step:view.step}));
    const pill=$('journey-state');pill.hidden=view.phase==='empty';pill.dataset.phase=view.phase;put(pill,view.phase==='empty'?'':j('state_'+(view.phase==='catalog'?'done':view.phase)));
    put($('journey-title'),j(view.phase==='empty'?'title_empty':live?'title_live':'title_done'));
    const target=j(f.cloud?'target_cloud':f.visual?'target_visual':'target_text');
    put($('journey-text'),{empty:j('text_empty'),queued:j('text_queued'),retrieving:j('text_retrieving'),generating:j('text_generating',{count}),done:j(f.fragments===1?'text_done_one':'text_done_other',{count,target}),retrieved:j('text_search_only',{count}),catalog:j('text_catalog'),stopped:j('text_stopped'),error:j('text_error')}[view.phase]);
    const embedding=this.status?.embedding_model?this.status.embedding_model.split('/').at(-1)+' · '+this.status.embedding_device.toUpperCase():'—',join=parts=>parts.filter(Boolean).join(' · ')||'—';
    const route=!f.mode?'check_route':f.catalog?'route_catalog':f.searchOnly?'route_search_only':f.cloud?'route_cloud':(f.mode==='automatic'?'route_auto_':'route_manual_')+(f.visual?'visual':'text');
    const rows={
      vector:[j('check_vector'),embedding],
      fragments:[f.fragments?j(f.fragments===1?'check_fragments_one':'check_fragments_other',{count}):j('check_fragments'),f.fragments?join([f.similarity?j('check_cosine'):'',plural('documents',f.documents),f.similarity?j('check_best',{value:number(f.best,2)}):'']):'—'],
      route:[j(route),join([serverText(f.reason),f.images!=null?s('image_count',{count:f.images}):''])],
      answer:[j(view.phase==='retrieved'?'check_answer_none':'check_answer'),join([f.model,f.tokens!=null?number(f.tokens)+' tok':'',f.speed!=null?number(f.speed,2)+' tok/s':''])],
      cited:[j('check_cited'),f.cited&&!live?j(f.cited===1?'check_cited_one':'check_cited_other',{count:number(f.cited)}):turn&&!live&&turn.answer&&!f.searchOnly?j('check_cited_none'):'—']};
    for(const [key,[title,detail]] of Object.entries(rows)){const item=this.checks.get(key),state=view.checks[key];item.row.dataset.state=state;put(item.title,title);put(item.detail,detail);put(item.state,j('check_'+state));}
  }
  render(){
    const thread=$('chat-thread');thread.replaceChildren();this.elements.clear();if(!this.turns.some(turn=>turn.id===this.contextTurnId))this.contextTurnId=this.turns.at(-1)?.id||null;
    for(const turn of this.turns)this.update(turn);thread.hidden=!this.turns.length;$('chat-empty').hidden=Boolean(this.turns.length);this.context();this.controls();this.renderAttachments();this.jump();
  }
  history(before,topicId){return this.turns.slice(0,before).filter(turn=>turn.status==='done'&&(turn.topic_id||null)===(topicId||null)).slice(-3).flatMap(turn=>[{role:'user',content:turn.question.slice(0,3000)},{role:'assistant',content:(turn.answer||'').slice(0,3000)}]);}
  async send(question=$('question').value.trim(),retryId=null){
    if(this.active){this.stopping=true;this.controls();try{await this.api(`/api/jobs/${this.active}`,{method:'DELETE'});}catch(error){toast(error.message);this.stopping=false;this.controls();}return;}
    if(this.submitting||!question)return;
    const scope=$('chat-scope');if(scope.selectedOptions[0]?.disabled){toast(t('library.missing_scope'));return;}
    const previous=retryId?this.turns.find(turn=>turn.id===retryId):null,images=this.attachments.length?this.attachments:(retryId?this.turnImages.get(retryId)||[]:[]);
    if(previous?.attachment_names?.length&&!images.length){toast(s('reattach_image'));return;}
    this.submitting=true;this.controls();try{await this.beforeSend();}catch(error){this.submitting=false;this.controls();toast(error.message);return;}
    const topicId=previous?previous.topic_id:scope.value,turn=previous||{id:randomId(),question,topic_id:topicId||null,scope_label:topicId==='unassigned'?'unassigned':this.scopeName(),asked_at:new Date().toISOString()};if(!previous)this.turns.push(turn);this.contextTurnId=turn.id;
    Object.assign(turn,{answer:'',sources:[],metrics:{},status:'queued',job_id:null,model:null,elapsed_seconds:null,error_info:null,sent_at:Date.now(),attachment_names:images.map(image=>image.name)});this.turnImages.set(turn.id,images);this.render();
    // Bring the question that was just sent into view; a regenerated one may sit above the end.
    const thread=$('chat-thread');if(previous)this.elements.get(turn.id).article.scrollIntoView({block:'nearest'});else thread.scrollTop=thread.scrollHeight;this.jump();
    try{const result=await this.api('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,topic_id:turn.topic_id||null,language:getLanguage(),history:this.history(this.turns.indexOf(turn),turn.topic_id),images:images.map(image=>image.data)})});turn.job_id=result.job_id;if(!retryId){$('question').value='';this.autosize();}this.attachments=[];this.renderAttachments();this.save();this.submitting=false;await this.follow(turn);}
    catch(error){this.submitting=false;turn.status='error';turn.error_info={message:error.message};this.update(turn);this.save();this.controls();}
  }
  async follow(turn){
    if(this.active)return;this.active=turn.job_id;this.stopping=false;this.controls();
    try{while(true){const job=await this.api(`/api/jobs/${turn.job_id}`);window.dispatchEvent(new CustomEvent('rag:job',{detail:job}));Object.assign(turn,{status:job.status,phase:job.phase,answer:job.answer||'',sources:job.sources||[],error_info:job.error_info,metrics:job.metrics||{},model:job.model,elapsed_seconds:job.elapsed_seconds});this.update(turn);
      if(['done','error','cancelled'].includes(job.status)){this.save();announce(s(job.status==='done'?'answer_ready':'query_stopped'),'query-announcements');this.changed();break;}await new Promise(resolve=>setTimeout(resolve,700));}}
    catch(error){turn.status='error';turn.error_info={message:error.message};this.update(turn);this.save();}
    finally{this.active=null;this.submitting=false;this.stopping=false;this.controls();for(const item of this.turns)this.update(item);}
  }
  async restore(jobs){
    if(this.active)return;
    // The shared queue also contains other tabs/devices. Only resume this chat's
    // acknowledged requests, including jobs that finished while the tab was away.
    for(const turn of this.turns.filter(item=>busyStatus(item.status))){
      if(!turn.job_id){
        turn.status='error';turn.error_info={message:s('query_unconfirmed')};this.update(turn);this.save();continue;
      }
      const job=jobs.find(item=>item.id===turn.job_id);
      if(job&&'topic_id' in job)turn.topic_id=job.topic_id;
      if(job&&'scope_label' in job)turn.scope_label=job.scope_label;
      this.contextTurnId=turn.id;this.context();await this.follow(turn);
    }
  }
}
