const requested=new URLSearchParams(globalThis.location?.search||'').get('collection');
export const collectionId=/^[a-z][a-z0-9-]{0,47}$/.test(requested||'')?requested:(globalThis.sessionStorage?.getItem('musa:collection')||'default');
if(requested&&/^[a-z][a-z0-9-]{0,47}$/.test(requested))globalThis.sessionStorage?.setItem('musa:collection',collectionId);
export const sessionKey=key=>collectionId==='default'?key:`${key}:${collectionId}`;
export function collectionUrl(value){
  if(collectionId==='default')return value;
  const url=new URL(value,location.origin);
  if(url.origin===location.origin&&url.pathname.startsWith('/api/')&&!url.searchParams.has('collection'))url.searchParams.set('collection',collectionId);
  return url.pathname+url.search+url.hash;
}
