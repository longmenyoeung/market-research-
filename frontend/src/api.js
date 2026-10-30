let applicationKey='';
export function setApplicationKey(value) { applicationKey=value; }
export function websocketProtocols() {
  if(!applicationKey)return [];
  const encoded=btoa(String.fromCharCode(...new TextEncoder().encode(applicationKey))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
  return [`mca-auth.${encoded}`];
}
function browserOffline() { return typeof navigator!=='undefined'&&navigator.onLine===false; }
const offlineMessage='The browser is offline. In Chrome DevTools, set Network throttling to No throttling, then reload the page.';
let offlineRetryAt=0;
export async function api(path, options = {}) {
  const {background=false,...requestOptions}=options;
  if(!browserOffline())offlineRetryAt=0;
  if(background&&browserOffline()&&Date.now()<offlineRetryAt)throw new Error(offlineMessage);
  let response;
  try {
    response=await fetch(path, {...requestOptions, headers:{'Content-Type':'application/json', ...(applicationKey?{'X-API-Key':applicationKey}:{}), ...requestOptions.headers}, signal:requestOptions.signal || AbortSignal.timeout(180000)});
  } catch(error) {
    if(browserOffline()){offlineRetryAt=Date.now()+30000;throw new Error(offlineMessage);}
    if(error?.name==='TimeoutError'||error?.name==='AbortError')throw new Error(`Local request timed out: ${path}. Check that the app server is running.`);
    if(error instanceof TypeError)throw new Error(`The browser could not reach ${path}. Check the local app server and Chrome DevTools network settings.`);
    throw error;
  }
  if(response.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('mca:auth-required'));
  let body;
  try { body = await response.json(); } catch { throw new Error(`Server returned HTTP ${response.status}. Check the backend connection.`); }
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(d => `${d.loc?.slice(-1)}: ${d.msg}`).join('; ') : body.detail;
    throw new Error(body.error?.message || detail || `Request failed (${response.status})`);
  }
  return body;
}
export const post = (path, body) => api(path,{method:'POST',body:JSON.stringify(body)});
export const put = (path, body) => api(path,{method:'PUT',body:JSON.stringify(body)});
export const timeframes = ['1m','5m','15m','30m','1h','4h','1d'];
export function number(value, digits=2) { return value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined,{maximumFractionDigits:digits}); }
export function compact(value) { return value === null || value === undefined ? '—' : Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:2}).format(Number(value)); }
export function price(value) { return number(value, Number(value)<1?8:Number(value)<100?4:2); }
export function localTime(value) { return value ? new Date(value).toLocaleString() : '—'; }
export function classification(value, generatedAt, validUntilMs) {
  const generated = Date.parse(generatedAt);
  const expired = validUntilMs != null && (!Number.isFinite(validUntilMs) || Date.now() > validUntilMs);
  return expired || !Number.isFinite(generated) || Date.now()-generated > 120000 || generated-Date.now() > 5000 ? 'NO TRADE' : value || 'NO TRADE';
}
export function queryString(values) { const p=new URLSearchParams(); for(const [k,v] of Object.entries(values)) if(v!==''&&v!==undefined&&v!==null) p.set(k,String(v)); return p.toString(); }
export function downloadJson(filename,value) { const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})); const a=document.createElement('a'); a.href=url; a.download=filename; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
