import React,{useEffect,useRef,useState} from 'react';
import {BarChart3} from 'lucide-react';
import {api,post,localTime,price,number,queryString,timeframes} from '../api.js';
import {Badge,Busy,ErrorBox,Field,Stat} from './Common.jsx';
import MarketChart from './MarketChart.jsx';
import useLiveCandles from '../hooks/useLiveCandles.js';
import './IndicatorWorkspace.css';

const shortTermTimeframes=['15m','30m','1h','4h'];
const contextPlans={'15m':['1h','4h'],'30m':['1h','4h'],'1h':['4h'],'4h':['1d']};

export default function IndicatorWorkspace({markets,initialSymbol,timeframe,onTimeframeChange,onSimulate}){
 const [symbol,setSymbol]=useState(initialSymbol||''),[source,setSource]=useState('archive');
 const [rules,setRules]=useState({minimum_adx:20,minimum_relative_volume:1,atr_stop_multiple:1.5,signal_spacing_bars:8});
 const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState(null);
 const [archiveStatus,setArchiveStatus]=useState(null);
 const [profile,setProfile]=useState('men_crypto'),[higher,setHigher]=useState(null),[alerts,setAlerts]=useState(true);
 const [simulation,setSimulation]=useState({equity:10000,risk_percent:0.25,max_exposure_percent:100,reward_multiple:2,fee_bps:5,slippage_bps:5,spread_bps:2,funding_bps_per_8h:0,max_holding_bars:100,break_even:false,break_even_at_r:1,trailing_stop:false,trailing_atr:1.5,trailing_activation_r:1.5});
 const version=useRef(0),pending=useRef(false),studyRequest=useRef(null),refreshStudy=useRef(null),[,setTick]=useState(0);
 const autoLoad=useRef(false),loadedSelection=useRef('');
 useEffect(()=>{if(!symbol&&initialSymbol)setSymbol(initialSymbol);},[initialSymbol,symbol]);
 useEffect(()=>{version.current++;studyRequest.current?.abort();pending.current=false;setResult(null);setBusy(false);setError('');},[symbol,timeframe,source,profile]);
 useEffect(()=>()=>{version.current++;studyRequest.current?.abort();},[]);
 useEffect(()=>setHigher(null),[timeframe]);
 useEffect(()=>{
  if(source!=='archive'||!symbol)return;
  let active=true;const key=`${symbol}:${timeframe}`;setArchiveStatus({key,state:'loading'});
  api(`/api/v1/history/candles?${queryString({symbol,timeframe,limit:1})}`).then(data=>{if(active)setArchiveStatus({key,state:data.candles?.length?'available':'empty'});}).catch(error=>{if(active)setArchiveStatus({key,state:'error',message:error.message});});
  return()=>{active=false;};
 },[symbol,timeframe,source]);
 useEffect(()=>{const timer=setInterval(()=>setTick(value=>value+1),15000);return()=>clearInterval(timer);},[]);
 async function runStudy(background=false){
  if(!symbol||pending.current||archiveBlocked)return;const current=++version.current;pending.current=true;setBusy(true);setError('');if(!background)setResult(null);
  const controller=new AbortController();studyRequest.current=controller;
  try{const data=await post('/api/v1/indicator-study',{symbol,timeframe,source,rules,profile,higher_timeframes:selectedHigher.length?selectedHigher:undefined,simulation,dashboard_alerts:alerts},{signal:controller.signal});if(current===version.current){setResult(data);setSelected(previous=>background&&data.indicator_study.signals.some(signal=>signal.id===previous)?previous:data.indicator_study.signals.at(-1)?.id||null);}}
  catch(error){if(current===version.current)setError(error.message);}
  finally{if(current===version.current){pending.current=false;setBusy(false);}}
 }
 function build(event){event.preventDefault();autoLoad.current=true;loadedSelection.current=`${symbol}:${timeframe}`;runStudy();}
 refreshStudy.current=()=>runStudy(true);
 const liveEnabled=source==='live'&&result?.symbol===symbol&&result?.timeframe===timeframe;
 const feed=useLiveCandles({symbol,timeframe,enabled:liveEnabled,onRefresh:()=>refreshStudy.current?.()});
 useEffect(()=>{if(!liveEnabled)return;const timer=setInterval(()=>refreshStudy.current?.(),60000);return()=>clearInterval(timer);},[liveEnabled,symbol,timeframe]);
 const study=result?.indicator_study;
 const selectedHigher=higher??contextPlans[timeframe]??timeframes.filter(tf=>timeframes.indexOf(tf)>timeframes.indexOf(timeframe)).slice(0,2);
 const selectedSignal=study?.signals.find(signal=>signal.id===selected)||study?.signals.at(-1);
 const expired=study?.source==='live'&&Date.now()>result.valid_until_ms;
 const currentSignal=expired||(study?.source==='live'&&feed.stale)?'NO TRADE':study?.current_signal;
 const plannedContext=study?.timeframe_plan?.higher_timeframes||selectedHigher;
 const primaryTrend=study?.evaluation.BUY?.trend?'bullish':study?.evaluation.SELL?.trend?'bearish':'neutral';
 const archiveState=archiveStatus?.key===`${symbol}:${timeframe}`?archiveStatus.state:'loading';
 const archiveBlocked=source==='archive'&&archiveState!=='available';
 useEffect(()=>{
  const key=`${symbol}:${timeframe}`;
  if(!autoLoad.current||loadedSelection.current===key||archiveBlocked||!symbol)return;
  const timer=setTimeout(()=>{loadedSelection.current=key;refreshStudy.current?.();},300);
  return()=>clearTimeout(timer);
 },[symbol,timeframe,archiveBlocked,source]);
 const indicatorValue=name=>result?.indicators?.[name]?.value;
 const histogram=indicatorValue('macd_hist');
 function discard(){version.current++;studyRequest.current?.abort();pending.current=false;setResult(null);setBusy(false);setError('');}
 return <div className="indicator-workspace">
  <section className="panel"><h3>BUY / SELL evidence and automatic drawings</h3><p>Study confirmed candle patterns with EMA trend, RSI/MACD momentum, ADX and volume. Rules are experimental; no win rate or profitability is assumed.</p>
   <div className="study-timeframes"><div role="group" aria-label="Short-term chart timeframe">{shortTermTimeframes.map(value=><button key={value} type="button" aria-pressed={timeframe===value} onClick={()=>onTimeframeChange(value)}>{value}</button>)}</div><p>Chart: <strong>{timeframe}</strong>. Higher trend context: <strong>{plannedContext?.join(' + ')||'automatic'}</strong>.{timeframe==='4h'?' The daily timeframe supplies broader context.':''} {profile==='men_crypto'?'Men Crypto arrows require confirmed higher trends and UTC VWAP.':'Experimental arrows use the chart timeframe only.'} Current candidates also use the complete live report checks.</p></div>
   <form onSubmit={build} className="study-form">
    <Field label="Indicator contract"><select value={symbol} onChange={event=>setSymbol(event.target.value)}><option value="">Select a contract</option>{markets.map(m=><option key={m.symbol}>{m.symbol}</option>)}</select></Field>
    <Field label="Signal profile"><select value={profile} onChange={event=>setProfile(event.target.value)}><option value="men_crypto">Men Crypto Signal Pro</option><option value="experimental">Experimental EMA20/50/200</option></select></Field>
    <Field label="Study source"><select value={source} onChange={event=>setSource(event.target.value)}><option value="archive">Saved candles - historical replay</option><option value="live">Live report - current data required</option></select></Field>
    {[['minimum_adx','Minimum ADX',5,60,1],['minimum_relative_volume','Minimum relative volume',0.1,5,0.1],['atr_stop_multiple','ATR stop multiple',0.5,5,0.1],['signal_spacing_bars','Marker spacing (bars)',1,100,1]].map(([key,label,min,max,step])=><Field key={key} label={label}><input type="number" required min={min} max={max} step={step} value={rules[key]} onChange={event=>{setRules({...rules,[key]:event.target.value===''?'':Number(event.target.value)});discard();}}/></Field>)}
    {profile==='men_crypto'&&<Field label="Confirmed higher trends"><select multiple value={selectedHigher} onChange={event=>{setHigher([...event.target.selectedOptions].map(option=>option.value));discard();}}>{timeframes.filter(tf=>timeframes.indexOf(tf)>timeframes.indexOf(timeframe)).map(tf=><option key={tf}>{tf}</option>)}</select></Field>}
    <details className="study-options"><summary>Hypothetical sizing, exits, RSI and dashboard alerts</summary><p>Virtual positions only. Stop changes activate on the following bar. A stop at entry can still lose costs.</p><div className="form-grid">{Object.entries(simulation).filter(([,value])=>typeof value!=='boolean').map(([key,value])=><Field key={key} label={`Simulation ${key.replaceAll('_',' ')}`}><input type="number" required step="any" value={value} onChange={event=>{setSimulation({...simulation,[key]:event.target.value===''?'':Number(event.target.value)});discard();}}/></Field>)}{[['buy_rsi_min',50],['buy_rsi_max',70],['sell_rsi_min',30],['sell_rsi_max',50]].map(([key,fallback])=><Field key={key} label={key.replaceAll('_',' ')}><input type="number" min="0" max="100" step="any" required value={rules[key]??fallback} onChange={event=>{setRules({...rules,[key]:Number(event.target.value)});discard();}}/></Field>)}</div><label className="checkbox"><input type="checkbox" checked={simulation.break_even} onChange={event=>{setSimulation({...simulation,break_even:event.target.checked});discard();}}/>Virtual break-even stop</label><label className="checkbox"><input type="checkbox" checked={simulation.trailing_stop} onChange={event=>{setSimulation({...simulation,trailing_stop:event.target.checked});discard();}}/>Virtual trailing stop</label><label className="checkbox"><input type="checkbox" checked={alerts} onChange={event=>setAlerts(event.target.checked)}/>Dashboard alerts for fresh confirmed live studies only</label></details>
    <button className="primary" disabled={!symbol||busy||archiveBlocked||(profile==='men_crypto'&&!selectedHigher.length)}><BarChart3 size={16}/>{busy?'Building study...':'Build indicator chart'}</button>
   </form>
   {source==='archive'&&symbol&&archiveState==='empty'&&<p className="study-archive-status" role="status">No saved {timeframe} candles for {symbol}. This timeframe needs a successful market-data download. Larger candles cannot supply missing smaller candles.</p>}
   {source==='archive'&&symbol&&archiveState==='loading'&&<p className="muted" role="status">Checking saved {timeframe} candle availability...</p>}
   {source==='archive'&&archiveState==='error'&&<ErrorBox error={archiveStatus.message}/>}
   <ErrorBox error={error}/>{busy&&<Busy>{source==='archive'?'Calculating indicators from real saved candles...':'Checking live candles, quotes, costs and higher-timeframe context...'}</Busy>}
  </section>
  {study&&<>
   <div className="provider-banner"><div><strong>{study.historical?'HISTORICAL REPLAY - past signals only':'LIVE STUDY - automatic candle updates'}</strong><p>{expired?'This analysis expired. A successful refresh is required before a current direction is available.':study.current_reason}</p><small>Latest confirmed candle: {localTime(study.as_of)}. BUY/SELL arrows describe past closed-bar conditions.</small></div><Badge>{currentSignal}</Badge></div>
   <div className="stats-row"><Stat label="PAST BUY MARKERS" value={study.signals.filter(signal=>signal.side==='BUY').length}/><Stat label="PAST SELL MARKERS" value={study.signals.filter(signal=>signal.side==='SELL').length}/><Stat label="CURRENT ELIGIBILITY" value={currentSignal}/><Stat label="CONFIRMED CANDLES" value={result.candles.length}/></div>
   <section className="panel"><h3>Signal dashboard</h3><p>Values at the last confirmed study candle. Current eligibility remains subject to source freshness and all required checks.</p><div className="stats-row"><Stat label="TREND FILTER" value={primaryTrend.toUpperCase()}/><Stat label="RSI" value={number(indicatorValue(`rsi_${result.parameters?.indicators?.momentum_period||14}`),1)}/><Stat label="MACD STATUS" value={histogram==null?'unavailable':histogram>0?'bullish':histogram<0?'bearish':'neutral'}/><Stat label="ADX STRENGTH" value={number(indicatorValue('adx'),1)}/><Stat label="RELATIVE VOLUME" value={number(indicatorValue('relative_volume'),2)}/><Stat label="VOLUME STATUS" value={study.evaluation.BUY?.volume?'confirmed':'weak / unavailable'}/><Stat label="VWAP" value={price(indicatorValue('vwap'))}/><Stat label="SIMULATED TARGET R/R" value={`${simulation.reward_multiple}R gross`}/></div></section>
   <section className="panel"><h3>Higher-timeframe trend context</h3><p>{study.historical?(study.timeframe_plan?.historical_markers_include_higher_context?'Men Crypto arrows join higher candles at each historical signal close; these cards summarize the latest study time.':'Historical context uses only higher candles that closed by this study time. Experimental arrows remain single-timeframe observations.'):'These are confirmed higher-timeframe checks. Missing, stale and conflicting context remains explicit.'}</p><div className="study-context-grid">{(result.multi_timeframe||[]).filter(context=>context.role==='higher').map(context=>{
    const available=study.historical?context.historical_available:context.usable;
    const conflict=available&&['bullish','bearish'].includes(primaryTrend)&&['bullish','bearish'].includes(context.summary?.trend)&&primaryTrend!==context.summary.trend;
    return <article key={context.timeframe}><strong>{context.timeframe}</strong> <Badge>{available?(study.historical?'historical':'usable'):'unavailable'}</Badge><p>Trend: <Badge>{available?context.summary?.trend:'unknown'}</Badge> / Momentum: <Badge>{available?context.summary?.momentum:'unknown'}</Badge></p>{conflict&&<Badge tone="warning">Conflicts with chart trend</Badge>}{context.as_of&&<small>Closed by {localTime(context.as_of)}</small>}{!available&&<small>{context.error||'Higher-timeframe data is incomplete or stale.'}</small>}</article>;
   })}</div>{!(result.multi_timeframe||[]).some(context=>context.role==='higher')&&<p>No higher-timeframe context is available in this view.</p>}</section>
   <section className="panel study-evidence"><h3>Evidence at the last study candle</h3><p>All listed conditions must pass. EMA/VWAP price filters form one trend group; RSI and MACD form one momentum group. These are rule checks, not independent probability estimates.</p><div className="study-evidence-grid">{['BUY','SELL'].map(side=><div key={side}><strong>{side} conditions</strong>{Object.entries(study.evidence_groups).map(([key,description])=><p key={key}><Badge>{study.evaluation[side]?.[key]?'pass':'wait'}</Badge> <span title={description}>{key}: {description}</span></p>)}</div>)}</div></section>
   {result.paper_tracker&&<section className="panel"><div className="section-heading"><h3>Hypothetical position tracker</h3><button onClick={()=>onSimulate({symbol,timeframe,signal_profile:profile,study_rules:rules,higher_timeframes:selectedHigher,simulation,data_source:source,analysis_settings:result.parameters})}>Backtest these exact rules</button></div><p>This window replay starts flat. It does not read an exchange account or establish out-of-sample performance. Signals can be skipped while a virtual position is open.</p><div className="stats-row"><Stat label="VIRTUAL POSITION" value={result.paper_tracker.open_position?.side||'FLAT'}/><Stat label="MODELED CLOSED TRADES" value={result.paper_tracker.metrics.trades}/><Stat label="VIRTUAL NET P&L" value={number(result.paper_tracker.metrics.net_pnl)}/><Stat label="EXPOSURE CAP" value={`${simulation.max_exposure_percent}%`}/></div>{result.paper_tracker.open_position&&<p>Virtual entry {price(result.paper_tracker.open_position.entry)} / stop {price(result.paper_tracker.open_position.stop)} / target {price(result.paper_tracker.open_position.target)} / quantity {number(result.paper_tracker.open_position.quantity,8)}</p>}<div className="study-signal-list">{result.paper_tracker.events.slice(-10).reverse().map((event,index)=><p key={index}>{event.kind.replaceAll('_',' ')} / {event.side} / {price(event.price)} / confirmed {localTime(event.known_at)}</p>)}</div><p className="muted">Notifications recorded for this fresh live candle: {study.notifications_emitted?.length||0}. Archived replays never create current alerts.</p></section>}
   <section className="panel chart-panel"><div className="study-live-bar" aria-label="Chart market status"><div><strong>{symbol} / {timeframe}</strong><span>{study.historical?'Saved close':'Last received trade price'}: <b data-testid="chart-last-price">{price((!study.historical&&feed.candle||result.candles.at(-1))?.close)}</b> USDT</span></div><div><Badge>{study.historical?'Historical - prices do not update':feed.status}</Badge><small>{study.historical?`Saved candle: ${localTime(study.as_of)}`:feed.lastReceived?`Received: ${localTime(feed.lastReceived)}${feed.stale?' - stale':''}`:'Waiting for fresh public market data'}</small></div></div>{!study.historical&&<p className="muted">Open candles and last-price line update from real public data. Open candles are provisional. Indicators and confirmed drawings refresh after candle closes, reconnects and every 60 seconds.</p>}<MarketChart report={result} liveCandle={study.historical?null:feed.candle} studySignalId={selected}/></section>
   <section className="panel"><h3>Past signal references</h3><p>Select a historical marker to draw its observed close reference, ATR stop and gross 1R / 2R / 3R targets. A marker is known after that candle closes; no fill or outcome is assumed.</p>
    {selectedSignal&&<div className="study-reference"><strong>Past {selectedSignal.side} - {localTime(selectedSignal.known_at)}</strong><span>Reference {price(selectedSignal.reference)}</span><span>Stop {price(selectedSignal.stop)}</span>{selectedSignal.targets.map((target,index)=><span key={index}>TP{index+1} {price(target)}</span>)}</div>}
    {study.signals.length?<div className="study-signal-list">{study.signals.slice(-30).reverse().map(signal=><button key={signal.id} className={selectedSignal?.id===signal.id?'selected':''} aria-pressed={selectedSignal?.id===signal.id} onClick={()=>setSelected(signal.id)}><Badge>Past {signal.side}</Badge><span>{localTime(signal.known_at)}</span><span>Close {price(signal.reference)}</span><small>RSI {number(signal.rsi,1)} / ADX {number(signal.adx,1)} / RVOL {number(signal.relative_volume,2)}</small></button>)}</div>:<p>No candles met all configured conditions. No signals are fabricated.</p>}
   </section>
   <section className="panel"><details><summary>Rules, formulas and limitations</summary><p>Signals appear when all conditions first become true after a previously unaligned candle, with at least {study.rules.signal_spacing_bars} bars between displayed markers. Stop distance is {study.rules.atr_stop_multiple} ATR; targets are multiples of that initial distance. This is not a backtest.</p><ul>{study.limitations.map(text=><li key={text}>{text}</li>)}</ul><div className="study-formulas">{Object.entries(result.indicators).filter(([name])=>/^(ema_|rsi_|macd|adx$|atr$|relative_volume$)/.test(name)).map(([name,indicator])=><p key={name}><strong>{name}: {number(indicator.value,4)}</strong><span>{indicator.formula} / warm-up {indicator.warmup} bars / parameters {JSON.stringify(indicator.parameters)}</span></p>)}</div></details></section>
  </>}
 </div>;
}
