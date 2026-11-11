import {$, node} from './ui.js';
import {t, serverText} from './i18n.js';

const s=(key,values={})=>t(`sentinel.${key}`,values);
const fields={'response-provider':'response_provider',model:'response_model','external-model':'external_model','vision-model':'vision_model','max-images':'max_images','max-visuals':'max_visuals_per_document','search-only':'search_only',multimodal:'multimodal'};
const formatLabels={'.markdown':'MD','.jpeg':'JPG','.zip':'ZIP MinerU'};

export class SettingsController {
  constructor(api,changed){
    this.api=api;this.changed=changed;this.value=null;this.models=[];this.providers=[];this.credentials=null;this.version=0;this.saved=0;this.inFlight=null;this.error=null;this.timer=null;this.status=null;this.confirmed=false;this.flashTimer=null;
    for(const [id,key] of Object.entries(fields))$(id).addEventListener('change',event=>{
      const element=event.target;
      // A typed number is brought back into the range the server accepts.
      if(element.type==='number'){const typed=Math.round(Number(element.value)),fallback=this.value?.[key]??Number(element.min);element.value=String(Math.max(Number(element.min),Math.min(Number(element.max),element.value!==''&&Number.isFinite(typed)?typed:fallback)));}
      if(!element.checkValidity()){element.reportValidity();return;}
      this.patch({[key]:element.type==='checkbox'?element.checked:element.type==='number'?Number(element.value):element.value});
    });
    $('response-mode-control').addEventListener('click',event=>{const control=event.target.closest('[data-response-mode]');if(control)this.patch({response_mode:control.dataset.responseMode});});
    $('top-k').addEventListener('click',event=>{const control=event.target.closest('[data-k]');if(control)this.patch({k:Number(control.dataset.k)});});
    for(const group of $('settings-form').querySelectorAll('[role=radiogroup]'))group.addEventListener('keydown',event=>this.arrows(group,event));
    for(const stepper of $('settings-form').querySelectorAll('.cfg-stepper'))stepper.addEventListener('click',event=>this.step(stepper,event));
    $('composer-model').addEventListener('change',()=>{const model=$('composer-model').value;this.patch(model==='automatic'?{response_mode:'automatic'}:{response_mode:'manual',response_model:model.slice(7)});});
    $('settings-form').addEventListener('submit',event=>{event.preventDefault();this.flush().catch(()=>{});});
    $('retry-settings').addEventListener('click',()=>{this.error=null;this.flush().catch(()=>{});});
    $('save-api-key').addEventListener('click',()=>this.saveCredential());
    $('delete-api-key').addEventListener('click',()=>this.deleteCredential());
    $('unlock-credentials').addEventListener('click',()=>this.unlockCredentials());
    this.formats();
    window.addEventListener('rag:languagechange',()=>this.render());
  }
  arrows(group,event){
    const radios=[...group.querySelectorAll('[role=radio]')],index=radios.indexOf(document.activeElement),delta={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1}[event.key];
    const next=index<0?null:event.key==='Home'?radios[0]:event.key==='End'?radios.at(-1):delta?radios[(index+delta+radios.length)%radios.length]:null;
    if(!next)return;event.preventDefault();next.focus();next.click();
  }
  step(stepper,event){
    const control=event.target.closest('[data-step]'),input=stepper.querySelector('input');if(!control||input.disabled)return;
    // Larger steps land on round values (490, 480…) instead of drifting from the typed one.
    const delta=Number(control.dataset.step),size=Math.abs(delta),current=Number(input.value),next=delta>0?Math.floor(current/size)*size+size:Math.ceil(current/size)*size-size;
    input.value=String(Math.max(Number(input.min),Math.min(Number(input.max),next)));input.dispatchEvent(new Event('change',{bubbles:true}));
  }
  formats(){
    // The chips come from the same accept list that validates uploads.
    const labels=[];for(const extension of $('file-input').accept.split(',')){const label=formatLabels[extension]||extension.slice(1).toUpperCase();if(!labels.includes(label))labels.push(label);}
    $('settings-formats').replaceChildren(...labels.map(label=>node('span','',label)));
  }
  flash(){const box=$('settings-save');box.classList.add('is-visible');clearTimeout(this.flashTimer);this.flashTimer=setTimeout(()=>box.classList.remove('is-visible'),1800);}
  set(result){this.value={...result.settings};this.models=result.models||[];this.providers=result.providers||[];this.credentials=result.credentials||null;this.version=this.saved=0;this.error=null;this.confirmed=false;this.render();this.changed?.(this.value,false);}
  async saveCredential(){
    const provider=this.value?.response_provider,key=$('provider-api-key').value;if(!provider||provider==='ollama'||!key)return;
    try{const result=await this.api(`/api/credentials/${provider}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:key})});this.credentials=result.credentials;$('provider-api-key').value='';this.error=null;this.flash();}
    catch(error){this.error=error;}this.render();
  }
  async deleteCredential(){
    const provider=this.value?.response_provider;if(!provider||provider==='ollama')return;
    try{const result=await this.api(`/api/credentials/${provider}`,{method:'DELETE'});this.credentials=result.credentials;this.error=null;}
    catch(error){this.error=error;}this.render();
  }
  async unlockCredentials(){
    const passphrase=$('credential-passphrase').value;if(!passphrase)return;
    try{const result=await this.api('/api/credentials/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passphrase})});this.credentials=result.credentials;$('credential-passphrase').value='';this.error=null;}
    catch(error){this.error=error;}this.render();
  }
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
      this.saved=version;this.confirmed=true;if(this.version===version)this.value={...result.settings};this.changed?.(this.value,true);
    }).catch(error=>{this.error=error;throw error;}).finally(()=>{
      this.inFlight=null;this.render();
      if(!this.error&&this.saved===this.version)this.flash();
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
  radios(selector,selected){for(const control of document.querySelectorAll(selector)){const checked=selected(control);control.setAttribute('aria-checked',String(checked));control.tabIndex=checked?0:-1;}}
  render(){
    if(!this.value)return;
    const value=this.value,pending=this.saved<this.version,external=value.response_provider!=='ollama';
    for(const [id,key] of Object.entries(fields)){
      const element=$(id);if(element.type==='checkbox')element.checked=value[key];else if(element.tagName!=='SELECT'&&element.value!==String(value[key]))element.value=value[key];
    }
    $('model-selection').value=value.response_mode;
    const providerSelect=$('response-provider'),providerSignature=JSON.stringify(this.providers);
    if(providerSelect.dataset.options!==providerSignature){providerSelect.dataset.options=providerSignature;providerSelect.replaceChildren(...this.providers.map(provider=>{const option=node('option','',provider.label);option.value=provider.id;return option;}));}providerSelect.value=value.response_provider;
    this.select('model',this.models,value.response_model);this.select('vision-model',this.models.filter(model=>model.vision),value.vision_model);
    $('model').closest('.cfg-field').hidden=external;$('external-model').closest('.cfg-field').hidden=!external;
    for(const control of $('response-mode-control').querySelectorAll('button'))control.disabled=external;
    const composer=$('composer-model'),selected=value.response_mode==='automatic'?'automatic':'manual:'+value.response_model;
    const signature=JSON.stringify([this.models,selected,s('auto'),s('vision'),external,value.response_provider,value.external_model]);
    if(composer.dataset.options!==signature){composer.dataset.options=signature;composer.replaceChildren();if(external){const label=this.providers.find(item=>item.id===value.response_provider)?.label||value.response_provider;const option=node('option','',`${label} · ${value.external_model||s('no_models')}`);option.value='external';composer.append(option);}else{const option=node('option','',s('auto'));option.value='automatic';composer.append(option);for(const model of this.models){const option=node('option','',model.name+(model.vision?' · '+s('vision'):''));option.value='manual:'+model.name;composer.append(option);}if(value.response_mode==='manual'&&!this.models.some(model=>model.name===value.response_model)){const option=node('option','',value.response_model+' · '+s('unavailable'));option.value=selected;composer.append(option);}composer.value=selected;}}composer.disabled=external;
    this.radios('#response-mode-control [data-response-mode]',control=>control.dataset.responseMode===value.response_mode);
    this.radios('#top-k [data-k]',control=>Number(control.dataset.k)===value.k);
    [...$('k-preview').children].forEach((cell,index)=>cell.classList.toggle('is-on',index<value.k));
    $('response-mode').textContent=external?s('router_cloud'):s(value.response_mode==='automatic'?'router_auto':'router_manual');
    $('credential-panel').hidden=!external;$('credential-unlock').hidden=!external||!this.credentials?.locked;
    if(external){const configured=Boolean(this.credentials?.configured?.[value.response_provider]);$('credential-state').textContent=s(this.credentials?.locked?'store_locked':configured?'key_configured':'key_missing');$('credential-state').classList.toggle('success',configured&&!this.credentials?.locked);$('delete-api-key').disabled=!configured||this.credentials?.locked;$('save-api-key').disabled=Boolean(this.credentials?.locked);}
    // Visual limits only apply while multimodal processing is on.
    for(const id of ['max-images','max-visuals']){
      const input=$(id),current=Number(input.value);input.disabled=!value.multimodal;
      for(const control of input.parentElement.querySelectorAll('[data-step]'))control.disabled=!value.multimodal||(Number(control.dataset.step)<0?current<=Number(input.min):current>=Number(input.max));
    }
    const box=$('settings-save'),label=$('settings-message'),text=this.error?s('save_failed'):pending?s('saving'):this.confirmed?s('saved'):'';
    if(label.textContent!==text)label.textContent=text;
    box.classList.toggle('is-error',Boolean(this.error));if(this.error)box.title=serverText(this.error.message);else box.removeAttribute('title');
    $('retry-settings').hidden=!this.error;
    const sync=this.error?serverText(this.error.message):pending?s('saving'):'';$('settings-sync').textContent=sync;$('settings-sync').hidden=!sync;
    this.warning();
  }
}
