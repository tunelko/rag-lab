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
