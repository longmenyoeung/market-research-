import {describe,it,expect} from 'vitest';
import {chartPriceFormat,decimalPlaces} from './chartPrecision.js';
import {price} from './api.js';
import {validLiveCandle} from './hooks/useLiveCandles.js';

describe('Market chart price precision',()=>{
 it('keeps small coin prices and stop distances distinct',()=>{
  const format=chartPriceFormat([{open:'0.00812300',high:'0.00845600',low:'0.00800100',close:'0.00823400'}]);
  expect(format).toEqual({type:'price',precision:6,minMove:0.000001});
  expect((0.008234).toFixed(format.precision)).not.toBe((0.008001).toFixed(format.precision));
 });
 it('handles padded decimals, scientific notation and explicit tick size',()=>{
  expect(decimalPlaces('0.00120000')).toBe(4);
  expect(decimalPlaces('1.2e-8')).toBe(9);
  expect(chartPriceFormat([{open:'1.2e-8'}]).precision).toBe(9);
  expect(chartPriceFormat([], '0.000005')).toEqual({type:'price',precision:6,minMove:0.000005});
  expect(chartPriceFormat([], '0.0500')).toEqual({type:'price',precision:2,minMove:0.05});
 });
 it('uses a safe empty-data default and never displays a very small positive price as zero',()=>{
  expect(chartPriceFormat([])).toEqual({type:'price',precision:2,minMove:0.01});
  expect(price(0.000000001234)).not.toBe('0');
 });
});

describe('Selected market live candle validation',()=>{
 const now=1800000000000,open_time=Math.floor(now/900000)*900000;
 const candle={open_time,close_time:open_time+899999,open:'0.0081',high:'0.0085',low:'0.0080',close:'0.0084',volume:'20',is_closed:false};
 it('accepts a consistent provisional candle on the requested timeframe',()=>{
  expect(validLiveCandle(candle,'15m',now)).toBe(true);
 });
 it('rejects malformed, stale, future or wrong-interval updates',()=>{
  expect(validLiveCandle({...candle,close:'NaN'},'15m',now)).toBe(false);
  expect(validLiveCandle({...candle,close:'0.009'},'15m',now)).toBe(false);
  expect(validLiveCandle({...candle,volume:'-1'},'15m',now)).toBe(false);
  expect(validLiveCandle(candle,'30m',now)).toBe(false);
  expect(validLiveCandle(candle,'15m',now+1900000)).toBe(false);
  expect(validLiveCandle(candle,'15m',now-900000)).toBe(false);
 });
});
