const levelNames=['Reference','Stop','TP1','TP2','TP3'];
const positive=value=>Number.isFinite(value)&&value>0;

// Index of the signal candle when the levels are positive and correctly ordered, otherwise -1.
// The chart drawing and the current-entry panel share this check so they always agree.
function signalCandleIndex(candles,signal){
 if(!signal||!['BUY','SELL'].includes(signal.side)||![signal.reference,signal.stop,...(signal.targets||[])].every(positive)||signal.targets?.length!==3)return -1;
 const {reference,stop,targets}=signal;
 if(signal.side==='BUY'?!(stop<reference&&reference<targets[0]&&targets[0]<targets[1]&&targets[1]<targets[2]):!(stop>reference&&reference>targets[0]&&targets[0]>targets[1]&&targets[1]>targets[2]))return -1;
 return (candles||[]).findIndex(candle=>candle.open_time===signal.time);
}

// Shared line and region builder for one signal's reference, stop and 1R/2R/3R targets.
function signalDrawings(report,signal,{kind,regionKind,idPrefix,label}){
 const index=signalCandleIndex(report?.candles,signal);
 if(index<0)return [];
 const {reference,stop,targets}=signal;
 const end=report.candles[Math.min(index+20,report.candles.length-1)].open_time;
 const common={timeframe:signal.timeframe,confirmed:false,confirmed_at:signal.known_at,rule:signal.level_rule};
 const prices=[reference,stop,...targets];
 const lines=levelNames.map((name,position)=>({
  ...common,id:`${idPrefix}${signal.id}-${name}`,kind,level:position===0?'entry':position===1?'stop':'target',label:label(name),points:[{time:signal.time,price:prices[position]}],
 }));
 return [...lines,...[['risk',stop],['reward',targets[1]]].map(([region,price])=>({
  ...common,id:`${idPrefix}${signal.id}-${region}`,kind:regionKind,label:label(region),region,
  points:[{time:signal.time,price:reference},{time:end,price}],
 }))];
}

// The past signal whose levels are drawn: the selected one, or the latest.
export function selectedStudySignal(report,selectedId){
 const signals=report?.indicator_study?.signals||[];
 return signals.find(value=>value.id===selectedId)||signals.at(-1)||null;
}

export function studyDrawings(report,selectedId){
 const signal=selectedStudySignal(report,selectedId);
 return signalDrawings(report,signal,{kind:'study_level',regionKind:'study_region',idPrefix:'',label:name=>`Past ${signal?.side} ${name}`});
}

// Same rule as backend/app/services/study_alerts.py: the latest signal is current only when it
// closed on the latest confirmed candle and the (freshness-gated) current signal names its side.
// It must also have drawable levels on a candle present in `candles`.
export function currentEntrySignal(study,currentSignal,candles){
 const signal=study?.signals?.at(-1);
 if(!signal||study.historical||!['BUY','SELL'].includes(signal.side))return null;
 const candidate=`${signal.side} candidate`;
 if(signal.known_at!==study.as_of||study.current_signal!==candidate||currentSignal!==candidate)return null;
 return signalCandleIndex(candles,signal)<0?null:signal;
}

// The entry is the signal candle's close used as a reference; a manual fill happens later.
export function entryDrawings(report,signal){
 if(!signal)return [];
 return signalDrawings(report,signal,{kind:'entry_level',regionKind:'entry_region',idPrefix:'entry-',
  label:name=>name==='Reference'?`ENTRY ${signal.side} (ref close)`:name==='Stop'?'SL':levelNames.includes(name)?name:`ENTRY ${signal.side} ${name}`});
}
