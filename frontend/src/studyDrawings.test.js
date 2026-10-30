import {describe,it,expect} from 'vitest';
import {studyDrawings} from './studyDrawings.js';

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
