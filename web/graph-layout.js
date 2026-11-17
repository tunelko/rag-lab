// Group identical mentions only inside one evidence unit, without changing identities.
export function layoutGraph(rows,query=''){
  const term=query.trim().toLocaleLowerCase();
  const visible=rows.filter(row=>row.review==='accepted'&&row.verification==='supported'&&(!term||
    [row.subject_label,row.object_label,row.predicate,row.file].some(value=>String(value||'').toLocaleLowerCase().includes(term))));
  const groups=new Map(),nodes=new Map(),edges=[];
  for(const row of visible){
    if(!groups.has(row.unit))groups.set(row.unit,{id:row.unit,file:row.file,nodes:[]});
    const group=groups.get(row.unit),ends=[];
    for(const [label,kind] of [[row.subject_label,row.subject_kind],[row.object_label,row.object_kind]]){
      const key=JSON.stringify([row.unit,kind||'',label]);
      if(!nodes.has(key)){const node={id:key,label,kind:kind||'',unit:row.unit};nodes.set(key,node);group.nodes.push(node);}
      ends.push(key);
    }
    edges.push({id:row.id,source:ends[0],target:ends[1],row});
  }
  let index=0;
  for(const group of groups.values()){
    group.x=200+(index%3)*400;group.y=155+Math.floor(index/3)*310;
    for(let i=0;i<group.nodes.length;i++){
      const angle=-Math.PI/2+2*Math.PI*i/group.nodes.length;
      group.nodes[i].x=group.x+105*Math.cos(angle);group.nodes[i].y=group.y+85*Math.sin(angle);
    }
    index++;
  }
  return {nodes:[...nodes.values()],edges,groups:[...groups.values()],width:Math.max(400,Math.min(3,groups.size)*400),height:Math.max(360,Math.ceil(groups.size/3)*310+30)};
}

// Each passage is a small star, as in the design: its most connected entity in the middle and the others around
// it on an ellipse wide enough for their cards. Passages sit side by side and wrap into rows.
// Positions are written on the groups and nodes; returns the size of the whole map.
export function placeStars(groups,edges=[],{rowWidth=2400}={}){
  const degree=new Map();
  for(const edge of edges)for(const end of [edge.source,edge.target])degree.set(end,(degree.get(end)||0)+1);
  let x=0,y=0,rowHeight=0,width=0;
  for(const group of groups){
    const [hub,...around]=[...group.nodes].sort((a,b)=>(degree.get(b.id)||0)-(degree.get(a.id)||0)),count=around.length;
    const rx=Math.max(220,150+26*count),ry=Math.max(120,84+14*count),w=2*rx+250,h=2*ry+130;
    if(x&&x+w>rowWidth){x=0;y+=rowHeight;rowHeight=0;}
    group.x=x+w/2;group.y=y+h/2+16;group.rx=rx;group.ry=ry;
    hub.x=group.x;hub.y=group.y;
    // Half a step off the top, so nothing sits right above the middle card, where the name of the passage goes;
    // a single companion stands to its right.
    around.forEach((item,index)=>{const angle=count===1?0:-Math.PI/2+2*Math.PI*(index+.5)/count;item.x=group.x+rx*Math.cos(angle);item.y=group.y+ry*Math.sin(angle);});
    x+=w;rowHeight=Math.max(rowHeight,h);width=Math.max(width,x);
  }
  return {width:Math.max(400,width),height:Math.max(360,y+rowHeight)};
}
// One colour per type of entity: the most frequent types first, at most `limit`; the rest stay neutral.
export function kindTones(nodes,limit=5){
  const counts=new Map();
  for(const node of nodes){const kind=String(node.kind||'').toLocaleLowerCase();if(kind)counts.set(kind,(counts.get(kind)||0)+1);}
  return new Map([...counts].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,limit).map(([kind],index)=>[kind,index]));
}
// Does a relation match the search text and the chosen source? Used to dim, not to remove.
export function rowMatches(row,query='',file=''){
  const term=query.trim().toLocaleLowerCase();
  return (!file||row.file===file)&&(!term||[row.subject_label,row.object_label,row.predicate,row.file].some(value=>String(value||'').toLocaleLowerCase().includes(term)));
}
