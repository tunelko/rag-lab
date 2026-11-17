import {$,node} from './ui.js';
import {t} from './i18n.js';
import {collectionUrl} from './collection-scope.js';
import {layoutGraph} from './graph-layout.js';
const label=(key,values={})=>t(`graph.${key}`,values),NS='http://www.w3.org/2000/svg';
function svg(tag,attributes={},text){const item=document.createElementNS(NS,tag);for(const [key,value]of Object.entries(attributes))item.setAttribute(key,String(value));if(text!=null)item.textContent=text;return item;}
const short=(text,max=26)=>String(text).length>max?String(text).slice(0,max-1)+'…':String(text);
export class GraphMap{
  constructor(){
    this.rows=[];$('graph-search').addEventListener('input',()=>this.render(this.rows));
    $('graph-map-source').addEventListener('change',()=>this.render(this.rows));
    $('graph-zoom-in').addEventListener('click',()=>this.zoom(.8));$('graph-zoom-out').addEventListener('click',()=>this.zoom(1.25));$('graph-map-reset').addEventListener('click',()=>this.render(this.rows));
    this.box=$('graph-map');
  }
  render(rows){
    this.rows=rows;
    const files=[...new Set(rows.filter(row=>row.review==='accepted'&&row.verification==='supported').map(row=>row.file))].sort(),signature=JSON.stringify(files),picker=$('graph-map-source');
    if(this.sourceSignature!==signature){const selected=picker.value;picker.replaceChildren(node('option','',label('all_sources')),...files.map(file=>{const option=node('option','',short(file.split('/').at(-1),55));option.value=file;option.title=file;return option;}));picker.firstElementChild.value='';if(files.includes(selected))picker.value=selected;this.sourceSignature=signature;}
    this.data=layoutGraph(rows.filter(row=>!picker.value||row.file===picker.value),$('graph-search').value);
    $('graph-map-count').textContent=label('visible_connections',{count:this.data.edges.length});
    this.box.replaceChildren();this.positions=new Map(this.data.nodes.map(item=>[item.id,item]));
    if(!this.data.nodes.length){this.box.append(node('p','graph-map-empty',label('map_empty')));return;}
    const drawing=svg('svg',{viewBox:`0 0 ${this.data.width} ${this.data.height}`,role:'group','aria-label':label('map_label')});this.drawing=drawing;this.view=[0,0,this.data.width,this.data.height];
    const defs=svg('defs'),marker=svg('marker',{id:'graph-arrow',viewBox:'0 0 10 10',refX:38,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',class:'graph-arrow'}));defs.append(marker);drawing.append(defs);
    for(const group of this.data.groups){const caption=svg('text',{x:group.x,y:group.y-127,'text-anchor':'middle',class:'graph-group-caption'},short(group.file.split('/').at(-1),45));caption.append(svg('title',{},group.file));drawing.append(caption);}
    this.lines=[];
    for(const edge of this.data.edges){
      const control=svg('g',{class:'graph-map-edge'+(edge.row.basis==='inferred'?' is-inferred':''),tabindex:0,role:'button','aria-label':`${edge.row.subject_label} ${edge.row.predicate} ${edge.row.object_label}`});
      const line=svg('line',{'marker-end':'url(#graph-arrow)'}),hit=svg('line',{class:'graph-edge-hit'}),text=svg('text',{'text-anchor':'middle',class:'graph-edge-label'},short((edge.row.polarity==='negative'?'¬ ':'')+edge.row.predicate,30));control.append(hit,line,text,svg('title',{},edge.row.predicate));
      const open=()=>{drawing.querySelectorAll('.is-selected').forEach(item=>item.classList.remove('is-selected'));control.classList.add('is-selected');this.show(edge.row);};control.addEventListener('click',open);control.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();open();}});drawing.append(control);this.lines.push({edge,line,hit,text});
    }
    this.circles=[];
    for(const item of this.data.nodes){
      const control=svg('g',{class:'graph-map-node',tabindex:0,role:'button','aria-label':item.label});
      control.append(svg('circle',{r:28}),svg('text',{'text-anchor':'middle',y:4,class:'graph-node-symbol'},item.label.slice(0,1).toLocaleUpperCase()),svg('text',{'text-anchor':'middle',y:46,class:'graph-node-label'},short(item.label)),svg('title',{},item.label+(item.kind?' · '+item.kind:'')));
      const open=()=>this.neighbors(item);control.addEventListener('click',open);control.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();open();}});
      control.addEventListener('pointerdown',event=>{event.stopPropagation();const initial=this.point(event);let moved=false;control.setPointerCapture(event.pointerId);
        const move=next=>{const point=this.point(next);item.x+=point.x-initial.x;item.y+=point.y-initial.y;initial.x=point.x;initial.y=point.y;moved=true;this.paint();};
        const up=()=>{control.removeEventListener('pointermove',move);control.removeEventListener('pointerup',up);control.removeEventListener('pointercancel',up);if(moved)control.classList.add('is-selected');};control.addEventListener('pointermove',move);control.addEventListener('pointerup',up);control.addEventListener('pointercancel',up);
      });drawing.append(control);this.circles.push({item,control});
    }
    drawing.addEventListener('pointerdown',event=>{if(event.target!==drawing)return;const initial={x:event.clientX,y:event.clientY},view=[...this.view];drawing.setPointerCapture(event.pointerId);
      const move=next=>{const rect=drawing.getBoundingClientRect();this.view=[view[0]-(next.clientX-initial.x)*view[2]/rect.width,view[1]-(next.clientY-initial.y)*view[3]/rect.height,view[2],view[3]];this.applyView();};const up=()=>{drawing.removeEventListener('pointermove',move);drawing.removeEventListener('pointerup',up);drawing.removeEventListener('pointercancel',up);};drawing.addEventListener('pointermove',move);drawing.addEventListener('pointerup',up);drawing.addEventListener('pointercancel',up);
    });this.box.append(drawing);this.paint();
  }
  point(event){const point=this.drawing.createSVGPoint();point.x=event.clientX;point.y=event.clientY;return point.matrixTransform(this.drawing.getScreenCTM().inverse());}
  paint(){for(const {item,control} of this.circles)control.setAttribute('transform',`translate(${item.x} ${item.y})`);for(const {edge,line,hit,text}of this.lines){const a=this.positions.get(edge.source),b=this.positions.get(edge.target);for(const [key,value]of Object.entries({x1:a.x,y1:a.y,x2:b.x,y2:b.y})){line.setAttribute(key,value);hit.setAttribute(key,value);}text.setAttribute('x',(a.x+b.x)/2);text.setAttribute('y',(a.y+b.y)/2-7);}}
  zoom(factor){if(!this.drawing)return;const [x,y,width,height]=this.view,next=width*factor;if(next<150||next>6000)return;this.view=[x+(width-next)/2,y+(height-height*factor)/2,next,height*factor];this.applyView();}
  applyView(){this.drawing.setAttribute('viewBox',this.view.join(' '));}
  neighbors(item){const pane=$('graph-map-detail');pane.replaceChildren(node('h3','',item.label),node('p','meta',item.kind));for(const edge of this.data.edges.filter(edge=>[edge.source,edge.target].includes(item.id))){const button=node('button','btn btn--secondary',`${edge.row.subject_label} → ${edge.row.predicate} → ${edge.row.object_label}`);button.type='button';button.addEventListener('click',()=>this.show(edge.row));pane.append(button);}}
  show(row){const pane=$('graph-map-detail');pane.replaceChildren(node('p','graph-map-eyebrow',label('source_backed')),node('h3','',`${row.subject_label} → ${row.predicate} → ${row.object_label}`),node('p','meta',row.file+(row.page?' · '+row.page:'')),node('blockquote','',row.quote));if(row.verification_note)pane.append(node('p','meta',row.verification_note));const open=node('button','btn btn--secondary',label('open_evidence'));open.type='button';open.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('rag:document',{detail:{url:collectionUrl('/api/graph/source?unit='+row.unit),file:row.file,page:row.page||1,trigger:open}})));pane.append(open);}
}
