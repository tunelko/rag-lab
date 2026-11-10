import {node,number} from './ui.js';
import {chartSeries} from './chart-data.js';

const NS='http://www.w3.org/2000/svg';
const svg=(name,attrs={})=>{const element=document.createElementNS(NS,name);for(const [key,value] of Object.entries(attrs))element.setAttribute(key,String(value));return element;};
export class ResourceChart {
  static setupDialog(){
    if(this.dialog)return;
    this.dialog=document.getElementById('resource-dialog');
    document.getElementById('resource-dialog-close').addEventListener('click',()=>this.dialog.close());
    this.dialog.addEventListener('close',()=>{
      const chart=this.expanded;if(!chart)return;
      chart.placeholder.replaceWith(chart.card);chart.placeholder=null;chart.card.classList.remove('resource-expanded');
      this.expanded=null;if(!document.querySelector('dialog[open]'))document.body.classList.remove('modal-open');
      chart.maximizeButton.focus({preventScroll:true});
    });
    this.dialog.addEventListener('click',event=>{const bounds=this.dialog.getBoundingClientRect();if(event.target===this.dialog&&(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom))this.dialog.close();});
  }
  constructor(id,key,label){
    ResourceChart.setupDialog();
    this.root=document.getElementById(id);this.key=key;this.label=label;this.index=null;
    this.card=this.root.closest('.resource-card');this.maximizeButton=this.card.querySelector('[data-maximize]');
    this.maximizeButton.addEventListener('click',()=>this.maximize());
    this.root.setAttribute('role','group');
    this.svg=svg('svg',{viewBox:'0 0 640 180',preserveAspectRatio:'none','aria-hidden':'true'});
    const defs=svg('defs');const gradient=svg('linearGradient',{id:`fill-${key}`,x1:0,y1:0,x2:0,y2:1});
    gradient.append(svg('stop',{offset:'0%','stop-color':'var(--resource-line)','stop-opacity':'.22'}),svg('stop',{offset:'100%','stop-color':'var(--resource-line)','stop-opacity':'.02'}));defs.append(gradient);
    this.grid=svg('g',{class:'resource-grid-lines'});
    for(let x=0;x<=10;x++)this.grid.append(svg('line',{x1:12+x*58.4,x2:12+x*58.4,y1:10,y2:168}));
    for(let y=0;y<=4;y++)this.grid.append(svg('line',{x1:12,x2:596,y1:10+y*39.5,y2:10+y*39.5}));
    this.paths=svg('g');this.axis=node('div','resource-y-axis');this.axis.setAttribute('aria-hidden','true');this.cross=svg('g',{class:'resource-crosshair',visibility:'hidden'});
    this.cursor=svg('line',{y1:10,y2:168});this.dot=svg('circle',{r:4});this.cross.append(this.cursor,this.dot);
    this.svg.append(defs,this.grid,this.paths,this.cross);this.root.append(this.svg,this.axis);
    this.tooltip=node('div','resource-tooltip');this.tooltip.id=`tip-${key}`;this.tooltip.hidden=true;this.tooltip.setAttribute('role','tooltip');this.root.append(this.tooltip);this.root.setAttribute('aria-describedby',this.tooltip.id);
    this.status=node('span','sr-only');this.status.setAttribute('role','status');this.root.append(this.status);
    this.root.addEventListener('pointermove',event=>{
      if(!this.series?.values.length)return;const bounds=this.svg.getBoundingClientRect();const x=(event.clientX-bounds.left)/bounds.width*640;
      let nearest=0;for(let i=1;i<this.series.values.length;i++)if(Math.abs(this.series.values[i].x-x)<Math.abs(this.series.values[nearest].x-x))nearest=i;
      if(Math.abs(this.series.values[nearest].x-x)>24){this.hide();return;}this.show(nearest);
    });
    this.root.addEventListener('pointerleave',()=>this.hide());
    this.root.addEventListener('blur',()=>this.hide());
    this.root.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))return;event.preventDefault();
      if(event.key==='Escape'){this.hide();return;}if(!this.series?.values.length)return;
      const count=this.series.values.length;let index=this.index??count-1;
      if(event.key==='Home')index=0;else if(event.key==='End')index=count-1;else index+=event.key==='ArrowLeft'?-1:1;
      this.show(Math.max(0,Math.min(count-1,index)));
      this.status.textContent=this.tooltip.textContent;
    });
  }
  format(value){return value==null?'—':`${number(value,1)} ${this.key==='ram'||this.key==='vram'?'GiB':'%'}`;}
  maximize(){
    if(ResourceChart.dialog.open)return;
    this.hide();this.placeholder=node('div','resource-placeholder');this.placeholder.style.height=`${this.card.getBoundingClientRect().height}px`;this.placeholder.setAttribute('aria-hidden','true');this.card.before(this.placeholder);
    document.getElementById('resource-dialog-title').textContent=`Rendimiento de ${this.label}`;
    document.getElementById('resource-dialog-body').append(this.card);this.card.classList.add('resource-expanded');ResourceChart.expanded=this;
    document.body.classList.add('modal-open');ResourceChart.dialog.showModal();document.getElementById('resource-dialog-close').focus({preventScroll:true});
  }
  hide(){this.index=null;this.tooltip.hidden=true;this.cross.setAttribute('visibility','hidden');}
  show(index){
    const point=this.series.values[index];this.index=index;this.cross.setAttribute('visibility','visible');
    this.cursor.setAttribute('x1',point.x);this.cursor.setAttribute('x2',point.x);this.dot.setAttribute('cx',point.x);this.dot.setAttribute('cy',point.y);
    this.tooltip.textContent=`${this.seconds>=86400?new Date(point.epoch).toLocaleString('es-ES'):new Date(point.epoch).toLocaleTimeString('es-ES')} · ${this.label}: ${this.format(point.value)}`;
    const fraction=this.svg.getBoundingClientRect().width/(this.root.getBoundingClientRect().width||1);
    this.tooltip.style.left=`${Math.max(3,Math.min(60,point.x/640*fraction*100))}%`;this.tooltip.hidden=false;
  }
  draw(history,end,seconds,max,gap=10000,summary=null){
    this.seconds=seconds;
    const selected=this.index==null?null:this.series?.values[this.index]?.epoch;
    this.hide();this.series=chartSeries(history,this.key,end-seconds*1000,end,max,gap);if(summary)Object.assign(this.series,summary);this.root.dataset.samples=this.series.values.length;
    this.paths.replaceChildren();
    for(const segment of this.series.segments){
      const d=segment.map((point,index)=>`${index?'L':'M'}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ');
      if(segment.length>1){this.paths.append(svg('path',{d:`${d} L${segment.at(-1).x.toFixed(2)},168 L${segment[0].x.toFixed(2)},168 Z`,class:'resource-area',fill:`url(#fill-${this.key})`}),svg('path',{d,class:'resource-line'}));}
      else this.paths.append(svg('circle',{cx:segment[0].x,cy:segment[0].y,r:3,class:'resource-single'}));
    }
    this.axis.replaceChildren();for(const value of [max,max/2,0])this.axis.append(node('span','',max>0?this.format(value):'—'));
    const empty=document.getElementById(`chart-${this.key}-empty`);empty.hidden=this.series.values.length>0;
    for(const [id,value] of [['min',this.series.min],['mean',this.series.mean],['max',this.series.max]])document.getElementById(`chart-${this.key}-${id}`).textContent=this.format(value);
    document.getElementById(`chart-${this.key}-range`).textContent=({60:'Hace 1 min',3600:'Hace 1 hora',86400:'Hace 1 día',604800:'Hace 1 semana'})[seconds]||`Hace ${seconds/60} min`;
    this.root.setAttribute('aria-label',`${this.label}. ${this.series.values.length} lecturas reales. Mínimo ${this.format(this.series.min)}, media ${this.format(this.series.mean)}, máximo ${this.format(this.series.max)}. Usa las flechas para recorrer las lecturas.`);
    if(selected!=null){const index=this.series.values.findIndex(point=>point.epoch===selected);if(index>=0)this.show(index);}
  }
}
