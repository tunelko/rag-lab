export function vramLevel(used,total,modelsLoaded=0){
  if(modelsLoaded>1)return 'crit';
  if(!Number.isFinite(used)||!Number.isFinite(total)||total<=0)return 'info';
  return used/total>.97?'crit':used/total>.90?'warn':'info';
}
