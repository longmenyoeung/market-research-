import {test,expect} from '@playwright/test';
import fs from 'node:fs';
const fixture=JSON.parse(fs.readFileSync(new URL('./report.fixture.json',import.meta.url),'utf8'));

async function setup(page,{offline=false,saved=false,keyRequired=false,restQuality='usable',planning=false,liveStudy=false}={}){
 const report=structuredClone(fixture);report.generated_at=new Date().toISOString();report.valid_until_ms=Date.now()+60000;
 if(planning){
  report.market_overview.quality.status='usable';report.assessment.classification='WAIT';
  const end=report.candles.at(-1).open_time,start=report.candles.at(-30).open_time;
  for(const [side,stop,targets] of [['LONG',95,[105,110,115]],['SHORT',105,[95,90,85]]]){
   const scenario=report.assessment.scenarios[side];Object.assign(scenario,{entry:100,entry_region:[99,101],stop,targets});
   for(const [label,value] of [['Entry',100],['Stop',stop],...targets.map((target,index)=>[`Target ${index+1}`,target])]){
    report.structure.annotations.push({id:`${side}-${label}`,kind:'plan',label:`${side} ${label}`,timeframe:'1h',rule:'Synthetic browser test plan',confirmed:false,points:[{time:end,price:value}]});
   }
   for(const [label,region,values] of [['Entry region','entry',[99,101]],['Risk','risk',[100,stop]],['Reward 2R','reward',[100,targets[1]]]]){
    report.structure.annotations.push({id:`${side}-${label}`,kind:'plan',label:`${side} ${label}`,timeframe:'1h',rule:'Synthetic browser test region',region,confirmed:false,points:[{time:start,price:values[0]},{time:end,price:values[1]}]});
   }
  }
 }
 let watchlist={name:'Favorites',symbols:[],timeframe:'1h'};
 const row={symbol:'TESTUSDT',timeframe:'1h',last_price:110,volume:10000000,change_percent:2,spread_bps:2,quality:'usable',liquidity:'normal',volatility:1.5,volatility_status:'normal',trend:'bullish',rsi:55,macd:'bullish',adx:28,structure:'neutral',long_score:55,short_score:0,regime:'trending',classification:'WAIT',warnings:[]};
 let backtest=null;
 await page.route('**/health',route=>route.fulfill({json:{status:'ok',version:'1.0.0',application_key_required:keyRequired}}));
 await page.route('**/api/v1/**',async route=>{const request=route.request(),url=new URL(request.url()),path=url.pathname;
  if(keyRequired&&request.headers()['x-api-key']!=='test-application-key')return route.fulfill({status:401,json:{detail:'A valid application X-API-Key is required.'}});
  if(path==='/api/v1/markets')return route.fulfill(offline||saved?{status:503,json:{error:{message:'Provider access unavailable in this test'}}}:{json:{contracts:[{symbol:'TESTUSDT'}],count:1}});
  if(path==='/api/v1/settings')return route.fulfill({json:report.parameters});
  if(path==='/api/v1/candles'){
   const openTime=Math.floor(Date.now()/3600000)*3600000;
   const candle={...report.candles.at(-1),open_time:openTime,close_time:openTime+3599999,is_closed:false};
   return route.fulfill({json:{provider:'binance_usdm',symbol:'TESTUSDT',timeframe:'1h',fetched_at:new Date().toISOString(),server_time:Date.now(),quality:{status:restQuality,missing_intervals:0,warnings:['Synthetic browser test data']},candles:[candle]}});
  }
  if(path==='/api/v1/history/candles')return route.fulfill({json:{symbol:'TESTUSDT',timeframe:'1h',source:'local_archive',live_data:false,candles:report.candles,quality:{status:'historical',missing_intervals:0,warnings:['Local archive only; not live data.']}}});
  if(path==='/api/v1/watchlist'){if(request.method()==='PUT')watchlist=request.postDataJSON();return route.fulfill({json:watchlist});}
  if(path==='/api/v1/scanner/refresh')return route.fulfill({json:{status:offline?'error':'complete'}});
  if(path==='/api/v1/scanner')return route.fulfill({json:offline?{status:'error',error:'access_restricted',message:'Provider access unavailable in this test',rows:[],total:0,processed:0,overview:{eligible:0}}:{status:saved?'cached':'complete',timeframe:'1h',message:saved?'Saved discovery; all data is stale.':undefined,total:1,processed:1,displayed:1,rows:[saved?{...row,quality:'stale',classification:'NO TRADE',long_score:null,short_score:null}:row],overview:{eligible:1,usable:saved?0:1,liquidity_warnings:0,long_candidates:0,short_candidates:0,mean_atr_percent:1.5}}});
  if(path==='/api/v1/analysis')return route.fulfill({json:report});
  if(path==='/api/v1/indicator-study'){
   if(request.postDataJSON().source==='live'&&!liveStudy)return route.fulfill({status:504,json:{error:{message:'Synthetic live provider timeout'}}});
   const studyReport=structuredClone(report);
   const signals=['BUY','SELL'].map((side,index)=>{
    const candle=report.candles.at(index===0?-40:-20),reference=Number(candle.close),risk=2,direction=side==='BUY'?1:-1;
    return {id:`study-${side}`,side,time:candle.open_time,known_at:candle.close_time,timeframe:'1h',reference,stop:reference-direction*risk,targets:[1,2,3].map(r=>reference+direction*risk*r),rsi:side==='BUY'?55:45,adx:25,relative_volume:1.2,rule:'Synthetic test marker',level_rule:'Synthetic ATR test guide'};
   });
   studyReport.indicator_study={source:'archive',historical:true,as_of:report.candles.at(-1).close_time,current_signal:'NO TRADE',current_reason:'Saved candles are historical; no current direction.',signals,evaluation:{BUY:{trend:true,momentum:true,strength:true,volume:true},SELL:{trend:false,momentum:false,strength:true,volume:true}},evidence_groups:{trend:'EMA alignment',momentum:'RSI and MACD',strength:'ADX',volume:'Relative volume'},rules:request.postDataJSON().rules,limitations:['Synthetic browser fixture; no live data.']};
   studyReport.multi_timeframe=[{timeframe:'4h',role:'higher',usable:false,historical_available:true,as_of:report.candles.at(-1).close_time,summary:{trend:'bearish',momentum:'bearish'}}];
   studyReport.indicator_study.profile=request.postDataJSON().profile;
   if(liveStudy&&request.postDataJSON().source==='live')Object.assign(studyReport.indicator_study,{source:'live',historical:false,current_reason:'Synthetic live study for browser testing only.'});
   studyReport.paper_tracker={hypothetical_only:true,open_position:null,metrics:{trades:2,net_pnl:3},events:[{kind:'virtual_tp',known_at:report.candles.at(-1).close_time,side:'LONG',price:110}]};
   studyReport.structure.annotations.push(...signals.map(signal=>({id:signal.id,kind:'study_signal',label:`Past ${signal.side}`,timeframe:'1h',confirmed:true,confirmed_at:signal.known_at,points:[{time:signal.time,price:signal.reference}],rule:signal.rule})));
   return route.fulfill({json:studyReport});
  }
  if(path==='/api/v1/history/reports')return route.fulfill({json:[{id:report.id,saved_at:report.generated_at,data:report}]});
  if(path==='/api/v1/backtests'){
   if(request.method()==='POST'){backtest={id:'test-run',status:'calculating',bars_loaded:120};return route.fulfill({status:202,json:backtest});}
   return route.fulfill({json:[]});
  }
  if(path==='/api/v1/backtests/test-run/cancel'){backtest={...backtest,status:'cancelled'};return route.fulfill({json:backtest});}
  if(path==='/api/v1/backtests/test-run')return route.fulfill({json:backtest});
  if(path==='/api/v1/notifications')return route.fulfill({json:[]});
  if(path==='/api/v1/planner')return route.fulfill({json:{risk_budget:10,quantity:1.95,notional:195,net_reward_to_risk:1.9,potential_loss:10,potential_profit:19,gross_reward_to_risk:2,violations:[],assumptions:['SYNTHETIC TEST RESULT'],constraint_source:'test fixture',inputs:request.postDataJSON()}});
  return route.fulfill({status:404,json:{error:{message:'Unexpected test request'}}});
 });
 await page.routeWebSocket('**/api/v1/ws/**',socket=>socket.send(JSON.stringify({type:'status',status:'blocked',terminal:true,code:'synthetic_test_no_live_stream'})));
}

test('offline provider is explicit and planner stays usable',async({page})=>{
 await setup(page,{offline:true});await page.goto('/');await expect(page.getByRole('heading',{name:'A clearer view of the market.'})).toBeVisible();await expect(page.getByText('Market provider unavailable',{exact:true})).toBeVisible();
 await expect(page.getByText('App running · market data unavailable')).toBeVisible();
 await expect(page.getByText('Discovery unavailable')).toBeVisible();
 await expect(page.getByText('No contracts were assessed because provider discovery failed.')).toBeVisible();
 await page.getByRole('button',{name:'Open offline planner'}).click();await page.getByLabel('Entry reference',{exact:true}).fill('100');await page.getByLabel('Stop reference',{exact:true}).fill('95');await page.getByLabel('Target reference',{exact:true}).fill('110');await page.getByRole('button',{name:'Calculate plan'}).click();await expect(page.getByText('SYNTHETIC TEST RESULT')).toBeVisible();
 await page.screenshot({path:'test-results/offline-planner.png',fullPage:true});
});

test('browser offline mode shows a clear message without repeated failed scanner requests',async({page})=>{
 await setup(page);await page.goto('/');
 await page.getByRole('button',{name:'Market scanner',exact:true}).click();
 await expect(page.getByRole('button',{name:'TESTUSDT PERPETUAL'})).toBeVisible();
 const failed=[];page.on('requestfailed',request=>failed.push(request.url()));
 await page.route('**/api/v1/scanner?*',route=>route.abort('internetdisconnected'));
 await page.context().setOffline(true);
 await page.getByLabel('Min. quote volume').fill('1');
 await expect(page.getByRole('alert')).toContainText('Network throttling to No throttling');
 const initialFailures=failed.length;
 await page.getByLabel('Min. quote volume').fill('2');
 await page.waitForTimeout(500);
 expect(failed.length).toBe(initialFailures);
});

test('offline planner draws validated user-supplied LONG and SHORT price maps',async({page})=>{
 await setup(page,{offline:true});await page.goto('/');await page.getByRole('button',{name:'Trade planner',exact:true}).click();
 await page.getByLabel('Entry reference',{exact:true}).fill('100');
 await page.getByLabel('Stop reference',{exact:true}).fill('95');
 await page.getByLabel('Target reference',{exact:true}).fill('110');
 await page.getByRole('button',{name:'Calculate plan'}).click();
 await expect(page.getByRole('img',{name:'Hypothetical LONG entry 100, stop 95, take profit 110'})).toBeVisible();
 await page.getByLabel('Direction').selectOption('SHORT');
 await expect(page.getByRole('img',{name:/Hypothetical LONG/})).toHaveCount(0);
 await page.getByLabel('Stop reference',{exact:true}).fill('105');
 await page.getByLabel('Target reference',{exact:true}).fill('90');
 await page.getByRole('button',{name:'Calculate plan'}).click();
 await expect(page.getByRole('img',{name:'Hypothetical SHORT entry 100, stop 105, take profit 90'})).toBeVisible();
});

test('saved market universe stays visible and clearly stale during a provider outage',async({page})=>{
 await setup(page,{saved:true});await page.goto('/');
 await expect(page.getByText('Saved market snapshot · data is stale')).toBeVisible();
 await expect(page.getByText('LAST DISCOVERED CONTRACTS')).toBeVisible();
 await page.getByRole('button',{name:'Market scanner',exact:true}).click();
 await expect(page.getByRole('button',{name:'TESTUSDT PERPETUAL'})).toBeVisible();
 await expect(page.locator('.scanner-table')).toContainText('NO TRADE');
});

test('saved universe opens historical candles without retrying live analysis or drawing trade levels',async({page})=>{
 let analysisRequests=0;
 await setup(page,{saved:true});page.on('request',request=>{if(new URL(request.url()).pathname==='/api/v1/analysis')analysisRequests++;});
 await page.goto('/');await page.getByRole('button',{name:'Market scanner',exact:true}).click();
 await page.getByRole('button',{name:'Analyze TESTUSDT',exact:true}).click();
 await expect(page.getByText('Saved candles - historical chart')).toBeVisible();
 await expect(page.locator('.chart-wrap canvas').first()).toBeVisible();
 await expect(page.getByText('NO TRADE',{exact:true}).last()).toBeVisible();
 await expect(page.locator('.plan-legend-card')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Read full report'})).toHaveCount(0);
 expect(analysisRequests).toBe(0);
});

test('dashboard startup uses the saved scanner universe without a failing live markets request',async({page})=>{
 const discoveryRequests=[],failedResponses=[];
 await setup(page,{saved:true});
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/v1/markets')discoveryRequests.push(request.url());});
 page.on('response',response=>{if(response.status()>=400)failedResponses.push(response.url());});
 await page.route('**/api/v1/scanner?*',route=>{
  if(new URL(route.request().url()).searchParams.has('minimum_volume'))return route.fulfill({json:{status:'cached',rows:[],universe_symbols:['TESTUSDT'],total:1,displayed:0,overview:{eligible:1,usable:0}}});
  return route.fallback();
 });
 await page.goto('/');await page.getByRole('button',{name:'Market scanner',exact:true}).click();
 await expect(page.getByRole('button',{name:'TESTUSDT PERPETUAL'})).toBeVisible();
 await page.getByLabel('Min. quote volume').fill('99999999999');
 await expect(page.getByText('0 shown / 1 eligible contracts')).toBeVisible();
 await page.getByRole('button',{name:'Chart workspace',exact:true}).click();
 await expect(page.getByLabel('Analysis contract')).toHaveValue('TESTUSDT');
 await page.getByRole('button',{name:'View saved candles'}).click();
 await expect(page.getByText('Saved candles - historical chart')).toBeVisible();
 expect(discoveryRequests).toEqual([]);expect(failedResponses).toEqual([]);
});

test('live analysis timeout falls back to saved candles without claiming a fresh setup',async({page})=>{
 await setup(page);await page.route('**/api/v1/analysis',route=>route.fulfill({status:504,json:{error:{message:'Public market-data request timed out.'}}}));
 await page.goto('/');await page.getByRole('button',{name:'Chart workspace',exact:true}).click();
 await page.getByRole('button',{name:'Generate analysis'}).click();
 await expect(page.getByText('Saved candles - historical chart')).toBeVisible();
 await expect(page.getByRole('alert')).toContainText('Public market-data request timed out.');
 await expect(page.locator('.chart-wrap canvas').first()).toBeVisible();
 await expect(page.locator('.plan-legend-card')).toHaveCount(0);
});

test('indicator studio draws historical BUY SELL studies and rejects a failed live refresh',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await setup(page,{saved:true});await page.goto('/');
 await page.getByRole('button',{name:'BUY / SELL indicator',exact:true}).click();
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByText('HISTORICAL REPLAY - past signals only')).toBeVisible();
 await expect(page.locator('.chart-wrap canvas').first()).toBeVisible();
 await expect(page.locator('.study-signal-list button')).toHaveCount(2);
 await expect(page.locator('.study-reference')).toContainText('Past SELL');
 await page.locator('.study-signal-list button').filter({hasText:'Past BUY'}).click();
 await expect(page.locator('.study-reference')).toContainText('Past BUY');
 await expect(page.locator('.study-reference')).toContainText('TP3');
 await expect(page.getByText('Conflicts with chart trend')).toBeVisible();
 await expect(page.locator('.indicator-workspace > .provider-banner')).toContainText('NO TRADE');
 await page.locator('.chart-wrap').scrollIntoViewIfNeeded();
 await page.screenshot({path:'test-results/historical-indicator-study.png'});
 await page.getByLabel('Study source').selectOption('live');
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByRole('alert')).toContainText('Synthetic live provider timeout');
 await expect(page.getByText('HISTORICAL REPLAY - past signals only')).toHaveCount(0);
 expect(errors).toEqual([]);
});

test('short-term indicator controls synchronize timeframes and discard the previous study',async({page})=>{
 await setup(page,{saved:true});await page.goto('/#indicator');
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByText('HISTORICAL REPLAY - past signals only')).toBeVisible();
 const controls=page.getByRole('group',{name:'Short-term chart timeframe'});
 for(const [timeframe,context] of [['15m','1h + 4h'],['30m','1h + 4h'],['1h','4h'],['4h','1d']]){
  await controls.getByRole('button',{name:timeframe,exact:true}).click();
  await expect(page.getByLabel('Primary timeframe')).toHaveValue(timeframe);
  await expect(controls.getByRole('button',{name:timeframe,exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.study-timeframes')).toContainText(`Higher trend context: ${context}`);
 }
 await expect(page.getByText('HISTORICAL REPLAY - past signals only')).toHaveCount(0);
 await controls.getByRole('button',{name:'30m',exact:true}).click();
 await page.route('**/api/v1/indicator-study',route=>{
  expect(route.request().postDataJSON().timeframe).toBe('30m');
  return route.fulfill({status:422,json:{error:{message:'Synthetic missing 30m archive'}}});
 });
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByRole('alert')).toContainText('Synthetic missing 30m archive');
});

test('missing 15m history is explicit and does not submit an impossible archive calculation',async({page})=>{
 await setup(page,{saved:true});let calculations=0;
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/v1/indicator-study')calculations++;});
 await page.route('**/api/v1/history/candles?*',route=>{
  if(new URL(route.request().url()).searchParams.get('timeframe')==='15m')return route.fulfill({json:{source:'local_archive',live_data:false,candles:[],quality:{status:'empty'}}});
  return route.fallback();
 });
 await page.goto('/#indicator');
 await page.getByRole('group',{name:'Short-term chart timeframe'}).getByRole('button',{name:'15m',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('No saved 15m candles');
 await expect(page.getByRole('button',{name:'Build indicator chart'})).toBeDisabled();
 await page.getByRole('group',{name:'Short-term chart timeframe'}).getByRole('button',{name:'30m',exact:true}).click();
 await expect(page.getByRole('button',{name:'Build indicator chart'})).toBeEnabled();
 expect(calculations).toBe(0);
});

test('live indicator updates prices, refreshes closed-bar analysis and unsubscribes on timeframe change',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await setup(page,{liveStudy:true});
 await page.route('**/api/v1/candles?*',route=>route.fulfill({json:{symbol:'TESTUSDT',timeframe:'1h',quality:{status:'stale'},candles:[]}}));
 let stream,requests=0;const closedStreams=[];
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/v1/indicator-study')requests++;});
 await page.routeWebSocket('**/api/v1/ws/**',socket=>{stream=socket;socket.onClose(()=>closedStreams.push(socket.url()));});
 await page.goto('/#indicator');
 await page.getByLabel('Study source').selectOption('live');
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByText('LIVE STUDY - automatic candle updates')).toBeVisible();
 await expect.poll(()=>Boolean(stream)).toBe(true);
 const openTime=Math.floor(Date.now()/3600000)*3600000;
 const candle={...fixture.candles.at(-1),open_time:openTime,close_time:openTime+3599999,open:'120',high:'125',low:'118',close:'121',is_closed:false};
 const send=(changes={},extra={})=>stream.send(JSON.stringify({type:'candle',symbol:'TESTUSDT',timeframe:'1h',event_time:Date.now(),stale:false,candle:{...candle,...changes},...extra}));
 const beforeClose=requests;
 send({open_time:openTime-3600000,close_time:openTime-1,is_closed:true});
 await expect.poll(()=>requests).toBeGreaterThan(beforeClose);
 send();
 await expect(page.getByTestId('chart-last-price')).toHaveText('121');
 await expect(page.getByLabel('Chart market status')).toContainText('streaming');
 await page.waitForTimeout(10);send({close:'123'});
 await expect(page.getByTestId('chart-last-price')).toHaveText('123');
 // Stale and out-of-order data cannot replace the last received price.
 send({close:'119'},{stale:true,event_time:Date.now()+1});
 await expect(page.getByLabel('Chart market status')).toContainText('stream stale');
 await expect(page.getByTestId('chart-last-price')).toHaveText('123');
 await page.waitForTimeout(10);send({close:'124'});
 await expect(page.getByTestId('chart-last-price')).toHaveText('124');
 const before=requests;
 // Gap/resync metadata must refresh even when no earlier bar can overwrite price.
 send({}, {resync_generation:1});await page.waitForTimeout(10);
 send({}, {resync_generation:2});
 await expect.poll(()=>requests).toBeGreaterThan(before);
 await expect(page.getByTestId('chart-last-price')).toHaveText('121');
 const afterResync=requests;
 await page.getByRole('group',{name:'Short-term chart timeframe'}).getByRole('button',{name:'30m',exact:true}).click();
 await expect(page.getByLabel('Primary timeframe')).toHaveValue('30m');
 await expect.poll(()=>requests).toBeGreaterThan(afterResync);
 await expect.poll(()=>closedStreams.some(url=>url.includes('timeframe=1h'))).toBe(true);
 expect(errors).toEqual([]);
});

test('live indicator uses REST fallback when its public stream is unavailable',async({page})=>{
 await setup(page,{liveStudy:true});await page.goto('/#indicator');
 await page.getByLabel('Study source').selectOption('live');
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByLabel('Chart market status')).toContainText('REST snapshots - 15s');
 await expect(page.getByText('Open candles and last-price line update from real public data.',{exact:false})).toBeVisible();
});

test('archive indicator never opens a price stream or presents saved candles as moving prices',async({page})=>{
 await setup(page,{saved:true});const streams=[];
 page.on('websocket',socket=>{if(new URL(socket.url()).pathname.startsWith('/api/v1/ws/'))streams.push(socket.url());});
 await page.goto('/#indicator');await page.getByRole('button',{name:'Build indicator chart'}).click();
 await expect(page.getByLabel('Chart market status')).toContainText('Historical - prices do not update');
 await expect(page.getByLabel('Chart market status')).toContainText('Saved close:');
 expect(streams).toEqual([]);
});

test('Men Crypto indicator hands the exact rules and risk settings to the separate strategy simulator',async({page})=>{
 await setup(page,{saved:true});await page.goto('/#indicator');
 await expect(page.getByRole('combobox',{name:'Signal profile',exact:true})).toHaveValue('men_crypto');
 await page.locator('summary').filter({hasText:'Hypothetical sizing, exits, RSI and dashboard alerts'}).click();
 await page.getByLabel('Simulation risk percent',{exact:true}).fill('0.5');
 await page.getByLabel('Simulation max exposure percent',{exact:true}).fill('20');
 await page.getByLabel('Minimum ADX',{exact:true}).fill('25');
 await page.getByLabel('buy rsi max',{exact:true}).fill('68');
 await page.getByLabel('Virtual break-even stop',{exact:true}).check();
 await page.getByLabel('Virtual trailing stop',{exact:true}).check();
 const firstRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/v1/indicator-study');
 await page.getByRole('button',{name:'Build indicator chart'}).click();
 const studyInput=(await firstRequest).postDataJSON();
 await expect(page.getByRole('heading',{name:'Hypothetical position tracker'})).toBeVisible();
 await expect(page.getByRole('heading',{name:'Signal dashboard',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Backtest these exact rules'}).click();
 await expect(page.getByLabel('Research strategy')).toHaveValue('indicator');
 await expect(page.getByLabel('Candle data source')).toHaveValue('archive');
 await expect(page.getByLabel('risk percent',{exact:true})).toHaveValue('0.5');
 await expect(page.getByLabel('Simulated break-even stop',{exact:true})).toBeChecked();
 const secondRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/v1/backtests'&&request.method()==='POST');
 await page.getByRole('button',{name:'Run historical simulation'}).click();
 const backtestInput=(await secondRequest).postDataJSON();
 expect(backtestInput.signal_profile).toBe(studyInput.profile);
 expect(backtestInput.study_rules).toEqual(studyInput.rules);
 expect(backtestInput.higher_timeframes).toEqual(studyInput.higher_timeframes);
 expect(backtestInput.max_exposure_percent).toBe(20);
 expect(backtestInput.break_even).toBe(true);expect(backtestInput.trailing_stop).toBe(true);
});

test('scanner, charts, reports, watchlist and history render without JS errors',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));await setup(page);await page.goto('/');
 await page.getByRole('button',{name:'Market scanner',exact:true}).click();await expect(page.getByRole('button',{name:'TESTUSDT PERPETUAL'})).toBeVisible();
 await page.getByRole('button',{name:'Watch TESTUSDT',exact:true}).click();await page.getByRole('button',{name:'Analyze TESTUSDT',exact:true}).click();
 await expect(page.locator('.chart-wrap canvas').first()).toBeVisible();await page.screenshot({path:'test-results/chart-workspace.png',fullPage:true});
 await page.locator('summary').filter({hasText:'Automatic drawings'}).click();
 await page.getByRole('button',{name:'Read full report'}).click();await expect(page.getByRole('heading',{name:'LONG scenario'})).toBeVisible();await expect(page.getByRole('heading',{name:'SHORT scenario'})).toBeVisible();
 await page.getByRole('button',{name:'Watchlist',exact:true}).click();await expect(page.getByRole('button',{name:'TESTUSDT',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Report history',exact:true}).click();await page.locator('.history-row').first().click();await expect(page.getByText('SAVED SNAPSHOT · HISTORICAL')).toBeVisible();
 expect(errors).toEqual([]);
});

test('selected chart labels REST snapshots when the public stream is blocked',async({page})=>{
 const request=page.waitForRequest(request=>request.url().includes('/api/v1/candles?')&&request.url().includes('symbol=TESTUSDT'));
 await setup(page);await page.goto('/');await page.getByRole('button',{name:'Chart workspace',exact:true}).click();
 await page.getByRole('button',{name:'Generate analysis'}).click();
 await request;
 await expect(page.getByText('REST snapshots · 15s')).toBeVisible();
 await expect(page.locator('.report-status [title]')).toHaveAttribute('title',/open candles are provisional/);
});

test('chart draws separate hypothetical LONG and SHORT entry, stop and take-profit levels',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await setup(page,{planning:true});await page.goto('/');await page.getByRole('button',{name:'Chart workspace',exact:true}).click();
 await page.getByRole('button',{name:'Generate analysis'}).click();
 await expect(page.locator('.plan-legend-card')).toHaveCount(2);
 await expect(page.locator('.plan-legend-card.long')).toContainText('TP3 115');
 await expect(page.locator('.plan-legend-card.short')).toContainText('TP3 85');
 await expect(page.locator('.chart-annotations text')).toHaveCount(0);
 await page.screenshot({path:'test-results/hypothetical-plan-chart.png',fullPage:true});
 const controls=page.getByRole('group',{name:'Chart scenario drawings'});
 await controls.getByRole('button',{name:'LONG',exact:true}).click();
 await expect(page.locator('.plan-legend-card')).toHaveCount(1);
 await expect(page.locator('.plan-legend-card.long')).toBeVisible();
 await expect(page.locator('.chart-annotations text')).toHaveCount(3);
 await page.screenshot({path:'test-results/hypothetical-long-chart.png',fullPage:true});
 await controls.getByRole('button',{name:'SHORT',exact:true}).click();
 await expect(page.locator('.plan-legend-card.short')).toBeVisible();
 await expect(page.locator('.chart-annotations text')).toHaveCount(3);
 await page.screenshot({path:'test-results/hypothetical-short-chart.png',fullPage:true});
 await controls.getByRole('button',{name:'Hide'}).click();
 await expect(page.locator('.plan-legend-card')).toHaveCount(0);
 expect(errors).toEqual([]);
});

test('stale REST snapshots are not shown as a live feed',async({page})=>{
 await setup(page,{restQuality:'stale'});await page.goto('/');await page.getByRole('button',{name:'Chart workspace',exact:true}).click();
 await page.getByRole('button',{name:'Generate analysis'}).click();
 await expect(page.getByText('REST data stale or incomplete')).toBeVisible();
});

test('mobile navigation remains reachable without body overflow',async({page})=>{
 await page.setViewportSize({width:390,height:844});await setup(page,{offline:true});await page.goto('/');await expect(page.locator('h1')).toBeVisible();
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+2);expect(overflow).toBe(false);
 await page.getByRole('button',{name:'Settings',exact:true}).click();await expect(page.getByLabel('Validated configuration (JSON)')).toBeVisible();
 await page.screenshot({path:'test-results/mobile-settings.png',fullPage:true});
});

test('changing primary timeframe resets incompatible context choices',async({page})=>{
 await setup(page);await page.goto('/');
 await page.getByRole('button',{name:'Chart workspace',exact:true}).click();
 await expect(page.getByLabel('Analysis contract')).toHaveValue('TESTUSDT');
 await page.getByLabel('Higher timeframes').selectOption(['4h']);
 await page.locator('.analysis-toolbar select').nth(2).selectOption('15m');
 await page.getByLabel('Primary timeframe').selectOption('4h');
 const sent=page.waitForRequest(request=>request.url().endsWith('/api/v1/analysis'));
 await page.getByRole('button',{name:'Generate analysis'}).click();
 const body=(await sent).postDataJSON();
 expect(body.timeframe).toBe('4h');expect(body.higher_timeframes).toEqual([]);expect(body.lower_timeframe).toBe(null);
});

test('application key unlocks in memory and can be locked again',async({page})=>{
 await setup(page,{keyRequired:true});await page.goto('/');
 await expect(page.getByRole('heading',{name:'Unlock your research workspace'})).toBeVisible();
 await page.getByLabel('Application access key').fill('wrong');
 await page.getByRole('button',{name:'Unlock workspace'}).click();
 await expect(page.getByText('A valid application X-API-Key is required.')).toBeVisible();
 await page.getByLabel('Application access key').fill('test-application-key');
 await page.getByRole('button',{name:'Unlock workspace'}).click();
 await expect(page.getByRole('heading',{name:'A clearer view of the market.'})).toBeVisible();
 await page.getByRole('button',{name:'Lock workspace'}).click();
 await expect(page.getByRole('heading',{name:'Unlock your research workspace'})).toBeVisible();
});

test('evidence replay submits settings and can cancel local calculation',async({page})=>{
 await setup(page);await page.goto('/');await page.getByRole('button',{name:'Validation lab'}).click();
 await expect(page.getByLabel('Research strategy')).toHaveValue('evidence');
 await expect(page.getByLabel('Funding data')).toHaveValue('historical');
 const sent=page.waitForRequest(request=>request.url().endsWith('/api/v1/backtests')&&request.method()==='POST');
 await page.getByRole('button',{name:'Run historical simulation'}).click();
 const body=(await sent).postDataJSON();
 expect(body.strategy).toBe('evidence');expect(body.funding_mode).toBe('historical');expect(body.analysis_settings).toBeTruthy();
 await expect(page.getByText('test-run')).toBeVisible();
 await page.getByRole('button',{name:'Cancel local simulation'}).click();
 await expect(page.getByText('cancelled',{exact:true})).toBeVisible();
});
