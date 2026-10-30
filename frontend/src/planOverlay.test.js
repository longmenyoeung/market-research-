import {describe,it,expect} from 'vitest';
import {selectPlanOverlays} from './planOverlay.js';

const NOW=1_800_000_000_000;

function report(){
 const scenarios={
  LONG:{side:'LONG',entry:100,entry_region:[99,101],stop:95,targets:[105,110,115]},
  SHORT:{side:'SHORT',entry:100,entry_region:[99,101],stop:105,targets:[95,90,85]},
 };
 const annotations=[];
 for(const [side,scenario] of Object.entries(scenarios)){
  for(const [label,value] of [['Entry',scenario.entry],['Stop',scenario.stop],...scenario.targets.map((target,index)=>[`Target ${index+1}`,target])]){
   annotations.push({kind:'plan',label:`${side} ${label}`,timeframe:'1h',rule:'Synthetic test rule',points:[{time:NOW,price:value}]});
  }
  for(const [label,region] of [['Entry region',scenario.entry_region],['Risk',[scenario.entry,scenario.stop]],['Reward 2R',[scenario.entry,scenario.targets[1]]]]){
   annotations.push({kind:'plan',label:`${side} ${label}`,timeframe:'1h',rule:'Synthetic test region',region:label,points:[{time:NOW-3600000,price:region[0]},{time:NOW,price:region[1]}]});
  }
 }
 return {manual_only:true,generated_at:new Date(NOW).toISOString(),valid_until_ms:NOW+60000,timeframe:'1h',
  market_overview:{quality:{status:'usable'}},assessment:{classification:'WAIT',scenarios},structure:{annotations}};
}

describe('hypothetical chart plans',()=>{
 it('draws independent LONG and SHORT levels and filters by selected side',()=>{
  const fresh=report();
  expect(selectPlanOverlays(fresh,'both',NOW).visibleSides).toEqual(['LONG','SHORT']);
  expect(selectPlanOverlays(fresh,'both',NOW).annotations).toHaveLength(16);
  expect(selectPlanOverlays(fresh,'LONG',NOW).annotations.every(a=>a.label.startsWith('LONG '))).toBe(true);
  expect(selectPlanOverlays(fresh,'SHORT',NOW).annotations.every(a=>a.label.startsWith('SHORT '))).toBe(true);
  expect(selectPlanOverlays(fresh,'off',NOW).annotations).toEqual([]);
 });

 it('hides plans when data, report time or classification is invalid',()=>{
  const fresh=report();
  expect(selectPlanOverlays(fresh,'both',NOW+60001).annotations).toEqual([]);
  expect(selectPlanOverlays(fresh,'both',NOW+120001).availableSides).toEqual([]);
  fresh.market_overview.quality.status='stale';
  expect(selectPlanOverlays(fresh,'both',NOW).annotations).toEqual([]);
  fresh.market_overview.quality.status='usable';fresh.assessment.classification='NO TRADE';
  expect(selectPlanOverlays(fresh,'both',NOW).annotations).toEqual([]);
 });

 it('rejects malformed price geometry and incomplete or unprovenanced annotations',()=>{
  const fresh=report();
  fresh.assessment.scenarios.SHORT.stop=90;
  expect(selectPlanOverlays(fresh,'both',NOW).availableSides).toEqual(['LONG']);
  fresh.structure.annotations=fresh.structure.annotations.filter(a=>a.label!=='LONG Target 3');
  expect(selectPlanOverlays(fresh,'both',NOW).availableSides).toEqual([]);
  fresh.structure.annotations.push({kind:'plan',label:'LONG Target 3',timeframe:'1h',points:[{time:NOW,price:115}]});
  expect(selectPlanOverlays(fresh,'both',NOW).availableSides).toEqual([]);
 });
});
