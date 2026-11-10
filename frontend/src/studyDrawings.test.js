import {describe,it,expect} from 'vitest';
import {studyDrawings,currentEntrySignal,entryDrawings,selectedStudySignal} from './studyDrawings.js';

const buy={id:'buy',side:'BUY',time:1000,known_at:1999,timeframe:'1m',reference:100,stop:95,targets:[105,110,115],level_rule:'Synthetic test guide'};
const sell={...buy,id:'sell',side:'SELL',time:2000,reference:100,stop:105,targets:[95,90,85]};
const report={candles:[{open_time:1000},{open_time:2000},{open_time:3000}],indicator_study:{signals:[buy,sell]}};
describe('historical indicator guides',()=>{
 it('uses the selected past signal and observed candle coordinates',()=>{
  const drawings=studyDrawings(report,'buy');
  expect(drawings.filter(value=>value.kind==='study_level')).toHaveLength(5);
  expect(drawings.find(value=>value.label==='Past BUY Stop').points[0].price).toBe(95);
  const risk=drawings.find(value=>value.region==='risk');
  expect(risk.points).toEqual([{time:1000,price:100},{time:3000,price:95}]);
  expect(risk.confirmed).toBe(false);
  expect(studyDrawings(report,'sell').find(value=>value.label==='Past SELL TP3').points[0].price).toBe(85);
 });
 it('rejects impossible or absent levels',()=>{
  expect(studyDrawings(null)).toEqual([]);
  expect(studyDrawings({...report,indicator_study:{signals:[{...buy,stop:101}]}})).toEqual([]);
  expect(studyDrawings({...report,indicator_study:{signals:[{...sell,targets:[95,90,-1]}]}})).toEqual([]);
 });
});

describe('current entry signal',()=>{
 const live={source:'live',historical:false,as_of:2999,current_signal:'SELL candidate',signals:[buy,{...sell,known_at:2999}]};
 it('returns the latest signal when it closed on the latest confirmed candle and the candidate matches',()=>{
  expect(currentEntrySignal(live,'SELL candidate',report.candles)?.id).toBe('sell');
 });
 it('returns null for NO TRADE, side mismatch, older signals, stale data and empty lists',()=>{
  const candles=report.candles;
  expect(currentEntrySignal({...live,current_signal:'NO TRADE'},'NO TRADE',candles)).toBeNull();
  expect(currentEntrySignal({...live,current_signal:'BUY candidate'},'BUY candidate',candles)).toBeNull();
  expect(currentEntrySignal({...live,as_of:3999},'SELL candidate',candles)).toBeNull();
  expect(currentEntrySignal(live,'NO TRADE',candles)).toBeNull();
  expect(currentEntrySignal({...live,signals:[]},'SELL candidate',candles)).toBeNull();
  expect(currentEntrySignal(null,'SELL candidate',candles)).toBeNull();
  expect(currentEntrySignal({...live,historical:true},'SELL candidate',candles)).toBeNull();
 });
 it('returns null when the chart could not draw the entry',()=>{
  const current={...sell,known_at:2999};
  expect(currentEntrySignal({...live,signals:[{...current,stop:99}]},'SELL candidate',report.candles)).toBeNull();
  expect(currentEntrySignal({...live,signals:[{...current,targets:[95,90]}]},'SELL candidate',report.candles)).toBeNull();
  expect(currentEntrySignal({...live,signals:[{...current,time:2500}]},'SELL candidate',report.candles)).toBeNull();
  expect(currentEntrySignal(live,'SELL candidate',[])).toBeNull();
 });
 it('selects the past signal by exact id, not by prefix',()=>{
  const prefixed={...report,indicator_study:{signals:[{...buy,id:'sell-1'},sell]}};
  expect(selectedStudySignal(prefixed,'sell').id).toBe('sell');
  expect(selectedStudySignal(prefixed,'missing').id).toBe('sell');
  expect(selectedStudySignal(null,'sell')).toBeNull();
 });
 it('labels entry, stop and target lines without implying a fill',()=>{
  const lines=entryDrawings(report,{...sell,known_at:2999}).filter(value=>!value.region);
  expect(lines.map(value=>value.label)).toEqual(['ENTRY SELL (ref close)','SL','TP1','TP2','TP3']);
  expect(lines.map(value=>value.level)).toEqual(['entry','stop','target','target','target']);
  expect(entryDrawings(report,null)).toEqual([]);
 });
});
