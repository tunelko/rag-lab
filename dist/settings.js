import {$, node, button, icon} from './ui.js';
import {t, serverText} from './i18n.js';

const s=(key,values={})=>t(`sentinel.${key}`,values);
const fields={model:'response_model','vision-model':'vision_model','max-images':'max_images','max-visuals':'max_visuals_per_document','search-only':'search_only',multimodal:'multimodal'};

export class SettingsController {
  constructor(api,changed){
    this.api=api;this.changed=changed;this.value=null;this.models=[];this.version=0;this.saved=0;this.inFlight=null;this.error=null;this.timer=null;this.status=null;
    for(const [id,key] of Object.entries(fields))$(id).addEventListener('change',event=>{
      const element=event.target;
      if(!element.checkValidity()){element.reportValidity();return;}
      this.patch({[key]:element.type==='checkbox'?element.checked:element.type==='number'?Number(element.value):element.value});
    });
    $('response-mode-control').addEventListener('click',event=>{const control=event.target.closest('[data-response-mode]');if(control)this.patch({response_mode:control.dataset.responseMode});});
    $('top-k').addEventListener('click',event=>{const control=event.target.closest('[data-k]');if(control)this.patch({k:Number(control.dataset.k)});});
    $('composer-model').addEventListener('change',()=>{const model=$('composer-model').value;this.patch(model==='automatic'?{response_mode:'automatic'}:{response_mode:'manual',response_model:model.slice(7)});});
    $('settings-form').addEventListener('submit',event=>{event.preventDefault();this.flush().catch(()=>{});});
    $('retry-settings').addEventListener('click',()=>{this.error=null;this.flush().catch(()=>{});});
    this.stepper('max-images');
    window.addEventListener('rag:languagechange',()=>this.render());
  }
  stepper(id){
    const input=$(id),wrapper=node('div','number-stepper');input.before(wrapper);
    for(const delta of [-1,1]){
      const control=button('','btn btn--secondary btn--icon',event=>{event.preventDefault();const next=Math.max(Number(input.min),Math.min(Number(input.max),Number(input.value)+delta));input.value=next;input.dispatchEvent(new Event('change',{bubbles:true}));});
      control.append(icon(delta<0?'minus':'plus'));control.dataset.step=String(delta);wrapper.append(control);if(delta<0)wrapper.append(input);
    }
  }
  set(result){this.value={...result.settings};this.models=result.models||[];this.version=this.saved=0;this.error=null;this.render();this.changed?.(this.value,false);}
  setStatus(status){this.status=status;this.warning();}
  warning(){ $('reindex-warning').hidden=!(this.status?.documents||[]).some(document=>document.needs_reindex); }
  patch(patch){
    if(!this.value)return;
    this.value={...this.value,...patch};this.version++;this.error=null;this.render();this.changed?.(this.value,false);
    clearTimeout(this.timer);this.timer=setTimeout(()=>this.save().catch(()=>{}),120);
  }
  save(){
    if(this.inFlight)return this.inFlight;
    if(this.saved===this.version)return Promise.resolve();
    const version=this.version,value={...this.value};this.error=null;
    this.inFlight=this.api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)}).then(result=>{
      this.saved=version;if(this.version===version)this.value={...result.settings};this.changed?.(this.value,true);
    }).catch(error=>{this.error=error;throw error;}).finally(()=>{
      this.inFlight=null;this.render();
      if(!this.error&&this.saved<this.version){clearTimeout(this.timer);this.timer=setTimeout(()=>this.save().catch(()=>{}),0);}
    });
    this.render();return this.inFlight;
  }
  async flush(){clearTimeout(this.timer);while(this.saved<this.version){await this.save();if(this.error)throw this.error;}if(this.inFlight)await this.inFlight;return this.value;}
  select(id,models,value){
    const select=$(id),signature=JSON.stringify([models,value,t('sentinel.vision'),t('sentinel.unavailable')]);
    if(select.dataset.options===signature)return;select.dataset.options=signature;select.replaceChildren();
    const available=models.some(model=>model.name===value)?models:[...(value?[{name:value,missing:true}]:[]),...models];
    for(const model of available){const option=node('option','',model.name+(model.missing?' · '+s('unavailable'):model.vision?' · '+s('vision'):''));option.value=model.name;select.append(option);}
    if(!available.length){const option=node('option','',s('no_models'));option.value='';select.append(option);}select.value=value||available[0]?.name||'';
  }
  render(){
    if(!this.value)return;
    const value=this.value,pending=this.saved<this.version;
    for(const [id,key] of Object.entries(fields)){
      const element=$(id);if(element.type==='checkbox')element.checked=value[key];else if(element.tagName!=='SELECT'&&element.value!==String(value[key]))element.value=value[key];
    }
    $('model-selection').value=value.response_mode;
    this.select('model',this.models,value.response_model);this.select('vision-model',this.models.filter(model=>model.vision),value.vision_model);
    const composer=$('composer-model'),selected=value.response_mode==='automatic'?'automatic':'manual:'+value.response_model;
    const signature=JSON.stringify([this.models,selected,s('auto'),s('vision')]);
    if(composer.dataset.options!==signature){composer.dataset.options=signature;composer.replaceChildren();const option=node('option','',s('auto'));option.value='automatic';composer.append(option);for(const model of this.models){const option=node('option','',model.name+(model.vision?' · '+s('vision'):''));option.value='manual:'+model.name;composer.append(option);}if(value.response_mode==='manual'&&!this.models.some(model=>model.name===value.response_model)){const option=node('option','',value.response_model+' · '+s('unavailable'));option.value=selected;composer.append(option);}composer.value=selected;}
    for(const control of document.querySelectorAll('[data-response-mode]'))control.setAttribute('aria-pressed',String(control.dataset.responseMode===value.response_mode));
    for(const control of document.querySelectorAll('#top-k [data-k]'))control.setAttribute('aria-pressed',String(Number(control.dataset.k)===value.k));
    [...$('k-preview').children].forEach((cell,index)=>cell.classList.toggle('is-active',index<value.k));
    $('response-mode').textContent=s(value.response_mode==='automatic'?'router_auto':'router_manual');
    const message=this.error?serverText(this.error.message):pending?s('saving'):s('autosaved');
    $('settings-message').textContent=message;$('settings-message').classList.toggle('form-error',Boolean(this.error));$('retry-settings').hidden=!this.error;
    $('settings-sync').textContent=message;$('settings-sync').hidden=!pending&&!this.error;
    for(const control of document.querySelectorAll('[data-step]'))control.setAttribute('aria-label',s(control.dataset.step==='-1'?'decrease_images':'increase_images'));
    this.warning();
  }
}
