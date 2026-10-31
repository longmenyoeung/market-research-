import {useEffect,useRef,useState} from 'react';
import {api,queryString,websocketProtocols} from '../api.js';

const intervals={'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1h':3600000,'4h':14400000,'1d':86400000};
const restricted=new Set(['access_restricted','provider_banned','rate_limited','unknown_contract','stream_rejected']);
const initial={candle:null,status:'disconnected',lastReceived:null,stale:true};

export function validLiveCandle(candle,timeframe,now=Date.now()) {
 if(!candle||!intervals[timeframe]||!Number.isFinite(candle.open_time)||!Number.isFinite(candle.close_time))return false;
 const [open,high,low,close,volume]=['open','high','low','close','volume'].map(key=>Number(candle[key]));
 return [open,high,low,close,volume].every(Number.isFinite)&&Math.min(open,high,low,close)>0&&volume>=0&&
  low<=Math.min(open,close)&&high>=Math.max(open,close)&&typeof candle.is_closed==='boolean'&&
  candle.close_time===candle.open_time+intervals[timeframe]-1&&candle.open_time<=now+5000&&
  now-candle.open_time<2*intervals[timeframe]+15000;
}

// Only the selected live market is subscribed. Archive views never use this feed.
export default function useLiveCandles({symbol,timeframe,enabled,onRefresh}) {
 const [feed,setFeed]=useState(initial),refreshRef=useRef(onRefresh);
 refreshRef.current=onRefresh;
 useEffect(()=>{
  setFeed(initial);
  if(!enabled||!symbol)return;
  let active=true,socket,reconnect,refreshTimer,attempt=0,loading=false,terminal=false,blocked=false;
  let lastStream=0,lastReceived=0,lastTime=-1,lastClosed=-1,wasClosed=false,generation=null,drops=0,lastEvent=-1;
  const controller=new AbortController();
  const update=value=>{if(active)setFeed(current=>({...current,...value}));};
  const refresh=()=>{if(!active||refreshTimer)return;refreshTimer=setTimeout(()=>{refreshTimer=null;if(active)refreshRef.current?.();},750);};
  const accept=(candle,status)=>{
   if(!validLiveCandle(candle,timeframe)||candle.open_time<lastTime||
      (candle.open_time===lastTime&&wasClosed&&!candle.is_closed))return false;
   const previous=lastTime;
   lastTime=candle.open_time;wasClosed=candle.is_closed;lastReceived=Date.now();
   update({candle,status,lastReceived,stale:false});
   if(candle.is_closed&&candle.open_time>lastClosed){lastClosed=candle.open_time;refresh();}
   else if(previous>=0&&candle.open_time>previous)refresh();
   return true;
  };
  const connect=()=>{
   if(!active||terminal)return;
   update({status:'connecting'});
   socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/v1/ws/candles/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe)}`,websocketProtocols());
   socket.onopen=()=>{attempt=0;};
   socket.onmessage=event=>{
    if(!active)return;
    let message;try{message=JSON.parse(event.data);}catch{return;}
    if((message.symbol&&message.symbol!==symbol)||(message.timeframe&&message.timeframe!==timeframe))return;
    if(message.type==='candle'){
     if(message.stale){update({status:'stream stale',stale:true});lastStream=0;return;}
     if(!Number.isFinite(message.event_time)||message.event_time<=lastEvent)return;
     if(Date.now()-message.event_time>20000||message.event_time>Date.now()+5000){update({status:'stream stale',stale:true});lastStream=0;return;}
     if(accept(message.candle,'streaming')){lastEvent=message.event_time;lastStream=Date.now();}
    }else{
     lastStream=0;update({status:message.code||message.status||'unavailable',stale:true});
    }
    if(generation!==null&&(message.resync_generation>generation||message.dropped_updates>drops))refresh();
    if(message.resync_generation!==undefined)generation=message.resync_generation;
    if(message.dropped_updates!==undefined)drops=message.dropped_updates;
    if(message.terminal||message.type==='error'){
     terminal=true;blocked=restricted.has(message.code);socket.close();
    }
   };
   socket.onerror=()=>{lastStream=0;update({status:'stream unavailable',stale:true});};
   socket.onclose=()=>{
    if(!active||terminal)return;
    lastStream=0;update({status:'reconnecting',stale:true});
    reconnect=setTimeout(connect,Math.min(30000,1000*2**Math.min(attempt++,5)));
   };
  };
  const poll=async()=>{
   if(!active||loading||blocked||Date.now()-lastStream<15000)return;
   loading=true;
   try{
    const snapshot=await api(`/api/v1/candles?${queryString({symbol,timeframe,limit:3})}`,{background:true,signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)])});
    if(!active||blocked||Date.now()-lastStream<15000)return;
    if(snapshot.symbol!==symbol||snapshot.timeframe!==timeframe||snapshot.quality?.status!=='usable'){
     update({status:'REST data stale or incomplete',stale:true});return;
    }
    if(!accept(snapshot.candles?.at(-1),'REST snapshots - 15s'))update({status:'REST data stale or incomplete',stale:true});
   }catch{if(active&&Date.now()-lastStream>=15000)update({status:'Live provider unavailable',stale:true});}
   finally{loading=false;}
  };
  connect();poll();
  const pollTimer=setInterval(poll,15000);
  const watchdog=setInterval(()=>{if(lastReceived&&Date.now()-lastReceived>20000)update({status:'No recent updates - stale',stale:true});},3000);
  return()=>{active=false;controller.abort();clearTimeout(reconnect);clearTimeout(refreshTimer);clearInterval(pollTimer);clearInterval(watchdog);socket?.close();};
 },[symbol,timeframe,enabled]);
 return feed;
}
