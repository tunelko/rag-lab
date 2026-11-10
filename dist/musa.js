// One CSS character, driven by actual request state. Finished avatars are static.
export function createMusa(state='idle',size='avatar',isStatic=true){
  const element=document.createElement('div');element.className=`musa musa--${size}`;element.dataset.state=state;element.classList.toggle('is-static',isStatic);element.setAttribute('aria-hidden','true');
  for(const part of ['orbit','eyes']){const child=document.createElement('div');child.className=`musa__${part}`;child.append(document.createElement('i'),document.createElement('i'));element.append(child);}return element;
}
