// Client correlation IDs work on private HTTP addresses as well as localhost.
// Server job IDs and session tokens are generated independently by the backend.
export function randomId(provider=globalThis.crypto){
  if(typeof provider?.randomUUID==='function')return provider.randomUUID();
  const bytes=new Uint8Array(16);provider.getRandomValues(bytes);
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=[...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
