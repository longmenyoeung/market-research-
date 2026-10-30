import React,{useEffect,useRef,useState} from 'react';
import {BarChart3} from 'lucide-react';
import {post,localTime,price,number} from '../api.js';
import {Badge,Busy,ErrorBox,Field,Stat} from './Common.jsx';
import MarketChart from './MarketChart.jsx';
import './IndicatorWorkspace.css';

export default function IndicatorWorkspace({markets,initialSymbol,timeframe,savedUniverse}){
 const [symbol,setSymbol]=useState(initialSymbol||''),[source,setSource]=useState('archive');
 const [rules,setRules]=useState({minimum_adx:20,minimum_relative_volume:1,atr_stop_multiple:1.5,signal_spacing_bars:8});
 const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState(null);
 const version=useRef(0),[,setTick]=useState(0);
 useEffect(()=>{if(!symbol&&initialSymbol)setSymbol(initialSymbol);},[initialSymbol,symbol]);
 useEffect(()=>{version.current++;setResult(null);setBusy(false);setError('');},[symbol,timeframe,source]);
 useEffect(()=>{const timer=setInterval(()=>setTick(value=>value+1),15000);return()=>clearInterval(timer);},[]);
 async function build(event){
  event.preventDefault();const current=++version.current;setBusy(true);setError('');setResult(null);
  try{const data=await post('/api/v1/indicator-study',{symbol,timeframe,source,rules});if(current===version.current){setResult(data);setSelected(data.indicator_study.signals.at(-1)?.id||null);}}
  catch(error){if(current===version.current)setError(error.message);}
  finally{if(current===version.current)setBusy(false);}
 }
 const study=result?.indicator_study;
 const selectedSignal=study?.signals.find(signal=>signal.id===selected)||study?.signals.at(-1);
 const expired=study?.source==='live'&&Date.now()>result.valid_until_ms;
 const currentSignal=expired?'NO TRADE':study?.current_signal;
 return <div className="indicator-workspace">
  <section className="panel"><h3>BUY / SELL evidence and automatic drawings</h3><p>Study confirmed candle patterns with EMA trend, RSI/MACD momentum, ADX and volume. Rules are experimental; no win rate or profitability is assumed.</p>
   <form onSubmit={build} className="study-form">
    <Field label="Indicator contract"><select value={symbol} onChange={event=>setSymbol(event.target.value)}><option value="">Select a contract</option>{markets.map(m=><option key={m.symbol}>{m.symbol}</option>)}</select></Field>
    <Field label="Study source"><select value={source} onChange={event=>setSource(event.target.value)}><option value="archive">Saved candles - historical replay</option><option value="live">Live report - current data required</option></select></Field>
    {[['minimum_adx','Minimum ADX',5,60,1],['minimum_relative_volume','Minimum relative volume',0.1,5,0.1],['atr_stop_multiple','ATR stop multiple',0.5,5,0.1],['signal_spacing_bars','Marker spacing (bars)',1,100,1]].map(([key,label,min,max,step])=><Field key={key} label={label}><input type="number" required min={min} max={max} step={step} value={rules[key]} onChange={event=>{setRules({...rules,[key]:event.target.value===''?'':Number(event.target.value)});setResult(null);version.current++;setBusy(false);}}/></Field>)}
    <button className="primary" disabled={!symbol||busy}><BarChart3 size={16}/>{busy?'Building study...':'Build indicator chart'}</button>
   </form>
   <ErrorBox error={error}/>{busy&&<Busy>{source==='archive'?'Calculating indicators from real saved candles...':'Checking live candles, quotes, costs and higher-timeframe context...'}</Busy>}
  </section>
  {study&&<>
   <div className="provider-banner"><div><strong>{study.historical?'HISTORICAL REPLAY - past signals only':'LIVE REPORT SNAPSHOT - refresh manually'}</strong><p>{expired?'This snapshot expired. Refresh before considering a current direction.':study.current_reason}</p><small>Latest confirmed candle: {localTime(study.as_of)}. BUY/SELL arrows describe past closed-bar conditions.</small></div><Badge>{currentSignal}</Badge></div>
   <div className="stats-row"><Stat label="PAST BUY MARKERS" value={study.signals.filter(signal=>signal.side==='BUY').length}/><Stat label="PAST SELL MARKERS" value={study.signals.filter(signal=>signal.side==='SELL').length}/><Stat label="CURRENT ELIGIBILITY" value={currentSignal}/><Stat label="CONFIRMED CANDLES" value={result.candles.length}/></div>
   <section className="panel study-evidence"><h3>Evidence at the last study candle</h3><p>All four conditions must pass for a marker. EMA values form one trend group; RSI and MACD form one momentum group. These are rule checks, not independent probability estimates.</p><div className="study-evidence-grid">{['BUY','SELL'].map(side=><div key={side}><strong>{side} conditions</strong>{Object.entries(study.evidence_groups).map(([key,description])=><p key={key}><Badge>{study.evaluation[side]?.[key]?'pass':'wait'}</Badge> <span title={description}>{key}: {description}</span></p>)}</div>)}</div></section>
   <section className="panel chart-panel"><MarketChart report={result} studySignalId={selected}/></section>
   <section className="panel"><h3>Past signal references</h3><p>Select a historical marker to draw its observed close reference, ATR stop and gross 1R / 2R / 3R targets. A marker is known after that candle closes; no fill or outcome is assumed.</p>
    {selectedSignal&&<div className="study-reference"><strong>Past {selectedSignal.side} - {localTime(selectedSignal.known_at)}</strong><span>Reference {price(selectedSignal.reference)}</span><span>Stop {price(selectedSignal.stop)}</span>{selectedSignal.targets.map((target,index)=><span key={index}>TP{index+1} {price(target)}</span>)}</div>}
    {study.signals.length?<div className="study-signal-list">{study.signals.slice(-30).reverse().map(signal=><button key={signal.id} className={selectedSignal?.id===signal.id?'selected':''} aria-pressed={selectedSignal?.id===signal.id} onClick={()=>setSelected(signal.id)}><Badge>Past {signal.side}</Badge><span>{localTime(signal.known_at)}</span><span>Close {price(signal.reference)}</span><small>RSI {number(signal.rsi,1)} / ADX {number(signal.adx,1)} / RVOL {number(signal.relative_volume,2)}</small></button>)}</div>:<p>No candles met all configured conditions. No signals are fabricated.</p>}
   </section>
   <section className="panel"><details><summary>Rules, formulas and limitations</summary><p>Signals appear when all conditions first become true after a previously unaligned candle, with at least {study.rules.signal_spacing_bars} bars between displayed markers. Stop distance is {study.rules.atr_stop_multiple} ATR; targets are multiples of that initial distance. This is not a backtest.</p><ul>{study.limitations.map(text=><li key={text}>{text}</li>)}</ul><div className="study-formulas">{Object.entries(result.indicators).filter(([name])=>/^(ema_|rsi_|macd|adx$|atr$|relative_volume$)/.test(name)).map(([name,indicator])=><p key={name}><strong>{name}: {number(indicator.value,4)}</strong><span>{indicator.formula} / warm-up {indicator.warmup} bars / parameters {JSON.stringify(indicator.parameters)}</span></p>)}</div></details></section>
  </>}
 </div>;
}
