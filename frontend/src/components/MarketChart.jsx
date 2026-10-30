import React, {useEffect,useRef,useState} from 'react';
import {createChart,CandlestickSeries,LineSeries,HistogramSeries,createSeriesMarkers} from 'lightweight-charts';
import {Empty} from './Common.jsx';

const palette=['#58d8ae','#e6b66a','#8c9eff','#53b9dc','#ce82d9','#ed8d87'];
export default function MarketChart({report,liveCandle}) {
  const host=useRef(null), svg=useRef(null), chartRef=useRef(null), [overlays,setOverlays]=useState(['ema_20','ema_50','ema_200','bb_upper','bb_lower','vwap']);
  const [groups,setGroups]=useState(['swing','structure','zone','trendline','fvg','previous_period']);
  const seriesNames=Object.keys(report?.series||{}).filter(n=>/^(ema_|sma_|bb_|vwap|supertrend|psar|ichimoku_|keltner_|donchian_)/.test(n));
  const toggle=(setter,current,name)=>setter(current.includes(name)?current.filter(v=>v!==name):[...current,name]);
  useEffect(()=>{
    if(!host.current||!report?.candles?.length) return;
    const chart=createChart(host.current,{autoSize:true,height:760,layout:{background:{color:'#101820'},textColor:'#8e9da9',fontFamily:'Inter, system-ui, sans-serif',fontSize:11,attributionLogo:true},grid:{vertLines:{color:'#1b2630'},horzLines:{color:'#1b2630'}},rightPriceScale:{borderColor:'#28343f'},timeScale:{borderColor:'#28343f',timeVisible:true},crosshair:{mode:0}});
    const candles=chart.addSeries(CandlestickSeries,{upColor:'#53cfa4',downColor:'#e87878',borderVisible:false,wickUpColor:'#53cfa4',wickDownColor:'#e87878'});
    candles.setData(report.candles.map(c=>({time:c.open_time/1000,open:Number(c.open),high:Number(c.high),low:Number(c.low),close:Number(c.close)})));
    overlays.forEach((name,i)=>{if(report.series?.[name]?.length){const s=chart.addSeries(LineSeries,{color:palette[i%palette.length],lineWidth:1,title:name,priceLineVisible:false,lastValueVisible:false});s.setData(report.series[name]);}});
    const volume=chart.addSeries(HistogramSeries,{color:'#365b57',priceFormat:{type:'volume'},priceLineVisible:false,lastValueVisible:false},1);
    volume.setData(report.candles.map(c=>({time:c.open_time/1000,value:Number(c.volume),color:Number(c.close)>=Number(c.open)?'#245d50':'#663c44'})));
    const rsiName=Object.keys(report.series||{}).find(k=>k===`rsi_${report.parameters?.indicators?.momentum_period}`)||'rsi_14';
    for(const [name,pane,color] of [[rsiName,2,'#b09bdd'],['macd',3,'#69c2ef'],['macd_signal',3,'#e6b66a'],['atr',4,'#71ceac']]){
      if(report.series?.[name]?.length){const s=chart.addSeries(LineSeries,{color,lineWidth:1,title:name,priceLineVisible:false,lastValueVisible:false},pane);s.setData(report.series[name]);if(pane===2){[30,70].forEach(price=>s.createPriceLine({price,color:'#495365',lineWidth:1,lineStyle:2,axisLabelVisible:false,title:String(price)}));}}
    }
    if(report.series?.macd_hist?.length){const s=chart.addSeries(HistogramSeries,{priceLineVisible:false,lastValueVisible:false},3);s.setData(report.series.macd_hist.map(d=>({...d,color:d.value>=0?'#286252':'#663b43'})));}
    chart.panes().forEach((pane,i)=>pane.setStretchFactor(i===0?4.4:i===1?0.7:1));
    const annotations=(report.structure?.annotations||[]).filter(a=>groups.includes(a.kind));
    const marks=annotations.filter(a=>['swing','pattern','sweep'].includes(a.kind)&&a.points?.length).map(a=>({time:a.points[0].time/1000,position:a.label.includes('H')?'aboveBar':'belowBar',shape:'circle',size:0.5,color:a.confirmed?'#a6b8c5':'#e6b66a',text:a.label})).sort((a,b)=>a.time-b.time);
    const markers=createSeriesMarkers(candles,marks);
    annotations.filter(a=>['plan','previous_period'].includes(a.kind)&&a.points?.length&&!a.region).forEach(a=>candles.createPriceLine({price:a.points[0].price,color:a.kind==='plan'?(a.label.includes('Stop')?'#e87878':'#57bba0'):'#b9a57d',lineWidth:1,lineStyle:2,axisLabelVisible:false,title:a.label}));
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
          const [p,q]=points;const color=a.region==='risk'?'#e87878':a.kind==='fvg'?'#7e92cc':a.label.toLowerCase().includes('resistance')?'#d08282':'#61baa2';
          if(a.region||['zone','fvg'].includes(a.kind)){add('rect',{x:Math.min(p.x,q.x),y:Math.min(p.y,q.y),width:Math.max(2,Math.abs(q.x-p.x)),height:Math.max(2,Math.abs(q.y-p.y)),fill:color,opacity:a.filled_as_of_latest_bar?'.05':'.12',stroke:color});}
          else add('line',{x1:p.x,y1:p.y,x2:q.x,y2:q.y,stroke:color,'stroke-width':1,'stroke-dasharray':a.confirmed?'':'4 3'});
        });svg.current.replaceChildren(fragment);
      });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(renderAnnotations);chart.subscribeCrosshairMove(renderAnnotations);
    host.current.addEventListener('wheel',renderAnnotations);host.current.addEventListener('pointerup',renderAnnotations);
    chart.timeScale().fitContent();renderAnnotations();
    chartRef.current={chart,candles,volume,lastTime:report.candles.at(-1).open_time/1000,renderAnnotations};
    const currentHost=host.current;
    return()=>{cancelAnimationFrame(raf);currentHost.removeEventListener('wheel',renderAnnotations);currentHost.removeEventListener('pointerup',renderAnnotations);markers.detach();chart.remove();chartRef.current=null;};
  },[report?.id,overlays.join(','),groups.join(',')]);
  useEffect(()=>{const instance=chartRef.current;if(instance&&liveCandle){const time=liveCandle.open_time/1000;if(time>=instance.lastTime){instance.candles.update({time,open:+liveCandle.open,high:+liveCandle.high,low:+liveCandle.low,close:+liveCandle.close});instance.volume.update({time,value:+liveCandle.volume,color:+liveCandle.close>=+liveCandle.open?'#245d50':'#663c44'});instance.lastTime=time;instance.renderAnnotations();}}},[liveCandle]);
  if(!report?.candles?.length)return <Empty title="Select a contract for chart analysis">A successful analysis loads actual candles, indicator panels and deterministic annotations.</Empty>;
  return <><div className="chart-controls"><details><summary>Indicator overlays ({overlays.length})</summary><div className="check-grid">{seriesNames.map(name=><label key={name}><input type="checkbox" checked={overlays.includes(name)} onChange={()=>toggle(setOverlays,overlays,name)}/>{name}</label>)}</div></details><details><summary>Automatic drawings ({groups.length})</summary><div className="check-grid">{['swing','structure','zone','trendline','sweep','retest','fvg','fibonacci','previous_period','plan','channel','divergence','pattern'].map(name=><label key={name}><input type="checkbox" checked={groups.includes(name)} onChange={()=>toggle(setGroups,groups,name)}/>{name}</label>)}</div></details><small>Price · Volume · RSI · MACD · ATR</small></div><div className="chart-wrap"><div ref={host} style={{height:760,width:'100%'}}/><svg ref={svg} className="chart-annotations" aria-hidden="true"/></div><p className="chart-credit">TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a>. Provisional drawings use conditional rules; they are not predictions.</p></>;
}
