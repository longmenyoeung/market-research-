import React, {useEffect,useRef,useState} from 'react';
import {createChart,CandlestickSeries,LineSeries,HistogramSeries,createSeriesMarkers} from 'lightweight-charts';
import {Empty} from './Common.jsx';
import {price} from '../api.js';
import {selectPlanOverlays} from '../planOverlay.js';
import {studyDrawings} from '../studyDrawings.js';
import {chartPriceFormat} from '../chartPrecision.js';
import './MarketChartPlan.css';

const palette=['#58d8ae','#e6b66a','#8c9eff','#53b9dc','#ce82d9','#ed8d87'];
export default function MarketChart({report,liveCandle,studySignalId}) {
  const host=useRef(null), svg=useRef(null), chartRef=useRef(null), [overlays,setOverlays]=useState(['ema_20','ema_50','ema_200','bb_upper','bb_lower','vwap']);
  const viewport=useRef(null);
  const [groups,setGroups]=useState(['swing','structure','zone','trendline','fvg','previous_period']),[planMode,setPlanMode]=useState('both');
  const isStudy=Boolean(report?.indicator_study);
  const studyLines=studyDrawings(report,studySignalId);
  const plans=selectPlanOverlays(report,planMode);
  const seriesNames=Object.keys(report?.series||{}).filter(n=>/^(ema_|sma_|bb_|vwap|supertrend|psar|ichimoku_|keltner_|donchian_)/.test(n));
  const toggle=(setter,current,name)=>setter(current.includes(name)?current.filter(v=>v!==name):[...current,name]);
  useEffect(()=>{
    if(!host.current||!report?.candles?.length) return;
    const marketKey=`${report.symbol}:${report.timeframe}`;
    const priceFormat=chartPriceFormat(report.candles,report.market_overview?.constraints?.tick_size);
    const chart=createChart(host.current,{autoSize:true,height:760,layout:{background:{color:'#101820'},textColor:'#8e9da9',fontFamily:'Inter, system-ui, sans-serif',fontSize:11,attributionLogo:true},grid:{vertLines:{color:'#1b2630'},horzLines:{color:'#1b2630'}},rightPriceScale:{borderColor:'#28343f'},timeScale:{borderColor:'#28343f',timeVisible:true},crosshair:{mode:0}});
    const planPrices=[...(isStudy?[]:plans.annotations),...studyLines].flatMap(a=>a.points.map(point=>point.price));
    const candles=chart.addSeries(CandlestickSeries,{priceFormat,upColor:'#53cfa4',downColor:'#e87878',borderVisible:false,wickUpColor:'#53cfa4',wickDownColor:'#e87878',
      autoscaleInfoProvider:original=>{const info=original();if(info&&planPrices.length){info.priceRange.minValue=Math.min(info.priceRange.minValue,...planPrices);info.priceRange.maxValue=Math.max(info.priceRange.maxValue,...planPrices);}return info;}});
    candles.setData(report.candles.map(c=>({time:c.open_time/1000,open:Number(c.open),high:Number(c.high),low:Number(c.low),close:Number(c.close)})));
    overlays.forEach((name,i)=>{if(report.series?.[name]?.length){const s=chart.addSeries(LineSeries,{priceFormat,color:palette[i%palette.length],lineWidth:1,title:name,priceLineVisible:false,lastValueVisible:false});s.setData(report.series[name]);}});
    const volume=chart.addSeries(HistogramSeries,{color:'#365b57',priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false},1);
    volume.setData(report.candles.map(c=>({time:c.open_time/1000,value:Number(c.volume),color:Number(c.close)>=Number(c.open)?'#245d50':'#663c44'})));
    const rsiName=Object.keys(report.series||{}).find(k=>k===`rsi_${report.parameters?.indicators?.momentum_period}`)||'rsi_14';
    for(const [name,pane,color] of [[rsiName,2,'#b09bdd'],['macd',3,'#69c2ef'],['macd_signal',3,'#e6b66a'],['atr',4,'#71ceac']]){
      if(report.series?.[name]?.length){const s=chart.addSeries(LineSeries,{priceFormat:pane===2?{type:'price',precision:2,minMove:0.01}:priceFormat,color,lineWidth:1,title:name,priceLineVisible:false,lastValueVisible:false},pane);s.setData(report.series[name]);if(pane===2){[30,70].forEach(price=>s.createPriceLine({price,color:'#495365',lineWidth:1,lineStyle:2,axisLabelVisible:false,title:String(price)}));}}
    }
    if(report.series?.macd_hist?.length){const s=chart.addSeries(HistogramSeries,{priceFormat,priceLineVisible:false,lastValueVisible:false},3);s.setData(report.series.macd_hist.map(d=>({...d,color:d.value>=0?'#286252':'#663b43'})));}
    chart.panes().forEach((pane,i)=>pane.setStretchFactor(i===0?4.4:i===1?0.7:1));
    const annotations=[...(report.structure?.annotations||[]).filter(a=>a.kind!=='plan'&&(a.kind==='study_signal'||groups.includes(a.kind))),...(isStudy?[]:plans.annotations),...studyLines];
    const marks=annotations.filter(a=>['swing','pattern','sweep','study_signal'].includes(a.kind)&&a.points?.length).map(a=>({time:a.points[0].time/1000,position:a.kind==='study_signal'?(a.label.includes('BUY')?'belowBar':'aboveBar'):a.label.includes('H')?'aboveBar':'belowBar',shape:a.kind==='study_signal'?(a.label.includes('BUY')?'arrowUp':'arrowDown'):'circle',size:a.kind==='study_signal'?1:0.5,color:a.kind==='study_signal'?(a.label.includes('BUY')?'#58d8ae':'#e87878'):a.confirmed?'#a6b8c5':'#e6b66a',text:a.label})).sort((a,b)=>a.time-b.time);
    const markers=createSeriesMarkers(candles,marks);
    annotations.filter(a=>['plan','previous_period','study_level'].includes(a.kind)&&a.points?.length&&!a.region).forEach(a=>{
      const sideColor=a.label.startsWith('SHORT ')?'#d991b9':'#66d6b0';
      const color=a.kind==='study_level'?(a.label.includes('Stop')?'#e6b66a':a.label.includes('BUY')?'#58d8ae':'#e87878'):a.kind==='plan'?(a.label.includes('Stop')?'#e6b66a':sideColor):'#b9a57d';
      candles.createPriceLine({price:a.points[0].price,color,lineWidth:a.kind==='plan'?2:1,lineStyle:2,
        axisLabelVisible:a.kind==='study_level'||a.kind==='plan'&&plans.visibleSides.length===1,
        title:a.kind==='plan'?a.label.replace('LONG ','L ').replace('SHORT ','S ').replace('Target ','TP '):a.label});
    });
    let raf;
    const renderAnnotations=()=>{
      cancelAnimationFrame(raf);raf=requestAnimationFrame(()=>{
        if(!svg.current) return;
        svg.current.style.height=`${chart.panes()[0].getHeight()}px`;const ns='http://www.w3.org/2000/svg'; const fragment=document.createDocumentFragment();
        const add=(tag,attrs)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));fragment.appendChild(e);return e;};
        annotations.filter(a=>a.region||['zone','fvg','trendline','structure','channel','fibonacci','divergence','retest'].includes(a.kind)).forEach(a=>{
          if(!a.points?.length)return;
          const points=a.points.map(p=>({x:chart.timeScale().timeToCoordinate(p.time/1000),y:candles.priceToCoordinate(p.price)}));
          if(points.some(p=>p.x===null||p.y===null)||points.length<2)return;
          const [p,q]=points;const color=a.region==='risk'?'#e87878':a.region==='reward'?'#57caa0':a.region==='entry'?(a.label.startsWith('SHORT ')?'#d991b9':'#66d6b0'):a.kind==='fvg'?'#7e92cc':a.label.toLowerCase().includes('resistance')?'#d08282':'#61baa2';
          if(a.region||['zone','fvg'].includes(a.kind)){
            const x=Math.min(p.x,q.x),y=Math.min(p.y,q.y);
            add('rect',{x,y,width:Math.max(2,Math.abs(q.x-p.x)),height:Math.max(2,Math.abs(q.y-p.y)),fill:color,opacity:a.filled_as_of_latest_bar?'.05':a.kind==='plan'?'.16':'.12',stroke:color});
            if(a.kind==='plan'&&plans.visibleSides.length===1){const label=add('text',{x:x+5,y:y+13,fill:color,'font-size':11,'font-weight':600});label.textContent=a.label;}
          }
          else add('line',{x1:p.x,y1:p.y,x2:q.x,y2:q.y,stroke:color,'stroke-width':1,'stroke-dasharray':a.confirmed?'':'4 3'});
        });svg.current.replaceChildren(fragment);
      });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(renderAnnotations);chart.subscribeCrosshairMove(renderAnnotations);
    host.current.addEventListener('wheel',renderAnnotations);host.current.addEventListener('pointerup',renderAnnotations);
    if(viewport.current?.key===marketKey&&viewport.current.range)chart.timeScale().setVisibleRange(viewport.current.range);
    else chart.timeScale().fitContent();
    renderAnnotations();
    chartRef.current={chart,candles,volume,lastTime:report.candles.at(-1).open_time/1000,renderAnnotations};
    const currentHost=host.current;
    return()=>{viewport.current={key:marketKey,range:chart.timeScale().getVisibleRange()};cancelAnimationFrame(raf);currentHost.removeEventListener('wheel',renderAnnotations);currentHost.removeEventListener('pointerup',renderAnnotations);markers.detach();chart.remove();chartRef.current=null;};
  },[report?.id,overlays.join(','),groups.join(','),plans.visibleSides.join(','),studySignalId]);
  useEffect(()=>{const instance=chartRef.current;if(instance&&liveCandle){const time=liveCandle.open_time/1000;if(time>=instance.lastTime){instance.candles.update({time,open:+liveCandle.open,high:+liveCandle.high,low:+liveCandle.low,close:+liveCandle.close});instance.volume.update({time,value:+liveCandle.volume,color:+liveCandle.close>=+liveCandle.open?'#245d50':'#663c44'});instance.lastTime=time;instance.renderAnnotations();}}},[liveCandle,report?.id,overlays.join(','),groups.join(','),plans.visibleSides.join(','),studySignalId]);
  if(!report?.candles?.length)return <Empty title="Select a contract for chart analysis">A successful analysis loads actual candles, indicator panels and deterministic annotations.</Empty>;
  return <>
   {report.source==='local_archive'?<p className="muted">Historical candlesticks and volume only. Indicators and automatic drawings require a fresh analysis.</p>:<div className="chart-controls"><details><summary>Indicator overlays ({overlays.length})</summary><div className="check-grid">{seriesNames.map(name=><label key={name}><input type="checkbox" checked={overlays.includes(name)} onChange={()=>toggle(setOverlays,overlays,name)}/>{name}</label>)}</div></details><details><summary>Automatic drawings ({groups.length})</summary><div className="check-grid">{['swing','structure','zone','trendline','sweep','retest','fvg','fibonacci','previous_period','channel','divergence','pattern'].map(name=><label key={name}><input type="checkbox" checked={groups.includes(name)} onChange={()=>toggle(setGroups,groups,name)}/>{name}</label>)}</div></details><small>Price · Volume · RSI · MACD · ATR</small></div>}
   {!isStudy&&<div className="plan-toolbar"><div><strong>Entry, stop & take profit</strong><small>Hypothetical · {report.timeframe} source · select a side for price labels · no order is sent</small></div><div className="plan-modes" role="group" aria-label="Chart scenario drawings">{[['both','Both'],['LONG','LONG'],['SHORT','SHORT'],['off','Hide']].map(([value,label])=><button key={value} type="button" aria-pressed={planMode===value} onClick={()=>setPlanMode(value)}>{label}</button>)}</div></div>}
   {!isStudy&&(plans.visibleSides.length?<div className="plan-legend">{plans.visibleSides.map(side=>{const scenario=report.assessment.scenarios[side];return <div key={side} className={`plan-legend-card ${side.toLowerCase()}`}><strong>{side} · hypothetical</strong><span>Entry {price(scenario.entry)} <small>({scenario.entry_region.map(price).join('–')})</small></span><span>Stop {price(scenario.stop)}</span>{scenario.targets.slice(0,3).map((target,index)=><span key={index}>TP{index+1} {price(target)}</span>)}</div>;})}</div>:<p className="plan-unavailable">{planMode==='off'&&plans.availableSides.length?'Scenario drawings are hidden. Select LONG, SHORT or Both to show them.':plans.reason}</p>)}
   {isStudy&&<p className="plan-unavailable">Arrows and level lines show past candle conditions. Select a past signal below to change its reference, stop and target drawings. These lines do not establish a current entry.</p>}
   <div className="chart-wrap"><div ref={host} style={{height:760,width:'100%'}}/><svg ref={svg} className="chart-annotations" aria-hidden="true"/></div><p className="chart-credit">TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a>. Provisional drawings and take-profit levels are hypothetical, not predictions or orders.</p>
  </>;
}
