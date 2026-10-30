export function studyDrawings(report,selectedId){
 const signals=report?.indicator_study?.signals||[];
 const signal=signals.find(value=>value.id===selectedId)||signals.at(-1);
 const positive=value=>Number.isFinite(value)&&value>0;
 if(!signal||!['BUY','SELL'].includes(signal.side)||![signal.reference,signal.stop,...(signal.targets||[])].every(positive)||signal.targets?.length!==3)return [];
 const {reference,stop,targets}=signal;
 if(signal.side==='BUY'?!(stop<reference&&reference<targets[0]&&targets[0]<targets[1]&&targets[1]<targets[2]):!(stop>reference&&reference>targets[0]&&targets[0]>targets[1]&&targets[1]>targets[2]))return [];
 const index=report.candles.findIndex(candle=>candle.open_time===signal.time);
 if(index<0)return [];
 const end=report.candles[Math.min(index+20,report.candles.length-1)].open_time;
 const common={timeframe:signal.timeframe,confirmed:false,confirmed_at:signal.known_at,rule:signal.level_rule};
 const lines=[['Reference',reference],['Stop',stop],...targets.map((value,index)=>[`TP${index+1}`,value])].map(([label,price])=>({
  ...common,id:`${signal.id}-${label}`,kind:'study_level',label:`Past ${signal.side} ${label}`,points:[{time:signal.time,price}],
 }));
 return [...lines,...[['risk',stop],['reward',targets[1]]].map(([region,price])=>({
  ...common,id:`${signal.id}-${region}`,kind:'study_region',label:`Past ${signal.side} ${region}`,region,
  points:[{time:signal.time,price:reference},{time:end,price}],
 }))];
}
