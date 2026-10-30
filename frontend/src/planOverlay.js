const SIDES=['LONG','SHORT'];
const positive=value=>typeof value==='number'&&Number.isFinite(value)&&value>0;

function validScenario(side,scenario){
 if(!scenario||scenario.side!==side||!positive(scenario.entry)||!positive(scenario.stop)||
    !Array.isArray(scenario.entry_region)||scenario.entry_region.length!==2||
    !scenario.entry_region.every(positive)||!Array.isArray(scenario.targets)||
    scenario.targets.length<3||!scenario.targets.every(positive))return false;
 const {entry,stop,entry_region:region,targets}=scenario;
 if(region[0]>entry||region[1]<entry)return false;
 return side==='LONG'
  ? stop<entry&&targets[0]>entry&&targets[0]<targets[1]&&targets[1]<targets[2]
  : stop>entry&&targets[0]<entry&&targets[0]>targets[1]&&targets[1]>targets[2];
}

function validAnnotation(annotation,timeframe){
 return annotation.kind==='plan'&&annotation.timeframe===timeframe&&
  typeof annotation.rule==='string'&&annotation.rule.length>0&&
  Array.isArray(annotation.points)&&annotation.points.length>0&&
  annotation.points.every(point=>Number.isFinite(point.time)&&positive(point.price));
}

export function selectPlanOverlays(report,mode='both',now=Date.now()){
 const empty=(reason)=>({availableSides:[],visibleSides:[],annotations:[],reason});
 if(!report)return empty('Generate an analysis to see hypothetical LONG and SHORT price levels.');
 const generated=Date.parse(report.generated_at),expiry=report.valid_until_ms;
 if(report.manual_only!==true||report.market_overview?.quality?.status!=='usable'||
    report.assessment?.classification==='NO TRADE'||!Number.isFinite(generated)||
    !Number.isFinite(expiry)||now>expiry||now-generated>120000||generated-now>5000){
  return empty('Fresh, usable analysis is required. Stale or NO TRADE reports never display entry and target drawings.');
 }
 const source=(report.structure?.annotations||[]).filter(annotation=>validAnnotation(annotation,report.timeframe));
 const availableSides=SIDES.filter(side=>{
  if(!validScenario(side,report.assessment?.scenarios?.[side]))return false;
  return ['Entry','Stop','Target 1','Target 2','Target 3'].every(label=>source.some(annotation=>annotation.label===`${side} ${label}`));
 });
 const visibleSides=mode==='both'?availableSides:availableSides.filter(side=>side===mode);
 return {availableSides,visibleSides,
  annotations:source.filter(annotation=>visibleSides.some(side=>annotation.label.startsWith(`${side} `))),
  reason:availableSides.length?'':'This report contains no complete hypothetical entry, stop and target levels.'};
}
