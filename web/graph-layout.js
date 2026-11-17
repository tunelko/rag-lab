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

// Isometric map: every passage is an island of floor cells with one entity per cell, and the islands tile the
// canvas like bricks. Positions are written on the groups and nodes; returns the size of the whole map.
export function placeIslands(groups,{cell=66,width=960}={}){
  const side=Math.max(2,...groups.map(group=>Math.ceil(Math.sqrt(group.nodes.length))));
  const islandWidth=2*side*cell,islandHeight=side*cell,pitchX=islandWidth+cell*.6,pitchY=islandHeight/2+cell*1.25;
  const columns=Math.max(1,Math.floor((width-islandWidth)/pitchX)+1),rows=Math.ceil(groups.length/columns);
  groups.forEach((group,index)=>{
    const row=Math.floor(index/columns);
    // The far corner of the island's diamond; odd rows shift half a step, so islands interlock.
    group.x=islandWidth/2+(index%columns)*pitchX+(row%2?pitchX/2:0);group.y=24+row*pitchY;group.side=side;
    group.nodes.forEach((item,position)=>{
      const u=position%side+.5,v=Math.floor(position/side)+.5;
      item.x=group.x+(u-v)*cell;item.y=group.y+(u+v)*cell/2;item.depth=row*1000+u+v;
    });
  });
  return {width:islandWidth+(columns-1)*pitchX+(rows>1?pitchX/2:0),height:24+Math.max(0,rows-1)*pitchY+islandHeight+44,cell,side};
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
