import {describe,it,expect,vi,afterEach} from 'vitest';
import {number,classification,queryString,api} from './api.js';
afterEach(()=>vi.unstubAllGlobals());
describe('Data honesty and API failures',()=>{
 it('never formats missing or nonfinite values as zero',()=>{expect(number(null)).toBe('—');expect(number(undefined)).toBe('—');expect(number(NaN)).toBe('—');expect(number(0)).toBe('0');});
 it('invalidates stale directional classifications',()=>{expect(classification('LONG candidate',new Date(Date.now()-130000).toISOString())).toBe('NO TRADE');expect(classification('WAIT',new Date().toISOString())).toBe('WAIT');});
 it('uses source deadlines and rejects invalid or future report timestamps',()=>{
  const now=Date.now();
  expect(classification('LONG candidate',new Date(now).toISOString(),now-1)).toBe('NO TRADE');
  expect(classification('LONG candidate',new Date(now).toISOString(),now+60000)).toBe('LONG candidate');
  expect(classification('LONG candidate','invalid')).toBe('NO TRADE');
  expect(classification('LONG candidate',new Date(now+60000).toISOString())).toBe('NO TRADE');
 });
 it('preserves explicit false and zero filters',()=>{const query=queryString({watchlist_only:false,minimum_volume:0,rsi_min:'',unknown:null});expect(query).toContain('watchlist_only=false');expect(query).toContain('minimum_volume=0');expect(query).not.toContain('rsi_min');});
 it('surfaces provider errors instead of returning fake data',async()=>{vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:503,json:async()=>({error:{message:'Provider denied access'}})})));await expect(api('/api/v1/markets')).rejects.toThrow('Provider denied access');});
 it('formats FastAPI validation details with and without loc',async()=>{
  const reply=detail=>vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:422,json:async()=>({detail})})));
  vi.stubGlobal('navigator',{onLine:true});
  reply([{loc:['body','symbol'],msg:'field required'},{loc:['query','limit',0],msg:'not an integer'}]);
  await expect(api('/api/v1/scanner')).rejects.toThrow('symbol: field required; 0: not an integer');
  reply([{msg:'invalid payload'},{loc:[],msg:'empty loc'}]);
  await expect(api('/api/v1/scanner')).rejects.toThrow(/^invalid payload; empty loc$/);
  reply([{loc:['body','symbol']},{type:'missing'},null]);
  await expect(api('/api/v1/scanner')).rejects.toThrow(/^symbol: Invalid value; Invalid value; Invalid value$/);
  reply('Symbol not supported');
  await expect(api('/api/v1/scanner')).rejects.toThrow(/^Symbol not supported$/);
 });
 it('explains browser offline mode and pauses repeated failed background reads',async()=>{
  const fetch=vi.fn(async()=>{throw new TypeError('Failed to fetch');});
  vi.stubGlobal('navigator',{onLine:false});vi.stubGlobal('fetch',fetch);
  await expect(api('/health',{background:true})).rejects.toThrow('Network throttling to No throttling');
  await expect(api('/api/v1/scanner',{background:true})).rejects.toThrow('Network throttling to No throttling');
  expect(fetch).toHaveBeenCalledTimes(1);
  await expect(api('/api/v1/watchlist')).rejects.toThrow('Network throttling to No throttling');
  expect(fetch).toHaveBeenCalledTimes(2);
  vi.stubGlobal('navigator',{onLine:true});
  await expect(api('/health')).rejects.toThrow('The browser could not reach /health');
  expect(fetch).toHaveBeenCalledTimes(3);
 });
 it('keeps loopback APIs usable when the OS reports no internet but localhost responds',async()=>{
  const fetch=vi.fn(async()=>({ok:true,status:200,json:async()=>({status:'ok'})}));
  vi.stubGlobal('navigator',{onLine:false});vi.stubGlobal('fetch',fetch);
  await expect(api('/health')).resolves.toEqual({status:'ok'});
  expect(fetch).toHaveBeenCalledTimes(1);
 });
 it('turns a local fetch failure into a useful connection error',async()=>{
  vi.stubGlobal('navigator',{onLine:true});vi.stubGlobal('fetch',vi.fn(async()=>{throw new TypeError('Failed to fetch');}));
  await expect(api('/api/v1/scanner')).rejects.toThrow('The browser could not reach /api/v1/scanner');
 });
});
