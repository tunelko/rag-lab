// One CSS character, driven by actual request state. Finished avatars are static.
const SVG='http://www.w3.org/2000/svg';
// Montera picona: [tag, class, attributes, children]. The same drawing as the static one in index.html.
const HAT=[
  ['path','musa__hat-body',{d:'M8 96 C8 84 20 77 36 75 C42 55 50 28 57 5 Q60 0 63 5 C68 30 74 56 80 75 C88 78 92 86 92 96 Q50 106 8 96 Z'}],
  ['path','musa__hat-seam',{d:'M60 7 C60 30 61 55 62 76'}],
  ['path','musa__hat-flap',{d:'M10 95 C20 86 34 82 48 84 L46 101 Q26 101 10 95 Z'}],
  ['path','musa__hat-trim',{d:'M10 95 C20 86 34 82 48 84'}],
  ['path','musa__hat-trim musa__hat-trim--brim',{d:'M8 96 Q50 106 92 96'}],
  ['g','musa__rosette',{},[
    ['path','musa__ribbon musa__ribbon--b',{d:'M67 88 L64 100'}],
    ['path','musa__ribbon musa__ribbon--y',{d:'M71 88 L73 100'}],
    ['circle','musa__ro-b',{cx:69,cy:86,r:6}],['circle','musa__ro-y',{cx:69,cy:86,r:4.3}],
    ['circle','musa__ro-b',{cx:69,cy:86,r:2.6}],['circle','musa__ro-y',{cx:69,cy:86,r:1.1}]]]];
// Small sizes keep only the silhouette and the red brim (.musa--sm); mid sizes drop the rosette (.musa--md).
const DETAIL={header:'musa--sm',avatar:'musa--sm',notice:'musa--md'};
function shape([tag,className,attributes,children=[]]){
  const element=document.createElementNS(SVG,tag);element.setAttribute('class',className);
  for(const [name,value] of Object.entries(attributes))element.setAttribute(name,value);
  element.append(...children.map(shape));return element;
}
export function createMusa(state='idle',size='avatar',isStatic=true){
  const element=document.createElement('div');element.className=`musa musa--${size}`;if(DETAIL[size])element.classList.add(DETAIL[size]);element.dataset.state=state;element.classList.toggle('is-static',isStatic);element.setAttribute('aria-hidden','true');
  for(const part of ['orbit','eyes']){const child=document.createElement('div');child.className=`musa__${part}`;child.append(document.createElement('i'),document.createElement('i'));element.append(child);}
  element.append(shape(['svg','musa__hat',{viewBox:'0 0 100 110','aria-hidden':'true'},HAT]));return element;
}
