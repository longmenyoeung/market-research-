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
});
