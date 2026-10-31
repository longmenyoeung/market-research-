// Display precision comes from observed OHLC decimals, never indicator floats.
// This is a display setting, not a claim about exchange order constraints.
export function decimalPlaces(value) {
 const text=String(value).toLowerCase();
 if(!Number.isFinite(Number(text)))return 0;
 const [coefficient,exponent='0']=text.split('e');
 const fraction=(coefficient.split('.')[1]||'').replace(/0+$/,'');
 return Math.max(0,fraction.length-Number(exponent));
}

export function chartPriceFormat(candles=[],tickSize) {
 const tick=Number(tickSize);
 const hasTick=Number.isFinite(tick)&&tick>0;
 const observed=candles.flatMap(c=>['open','high','low','close'].map(key=>decimalPlaces(c[key])));
 const precision=Math.min(12,hasTick?decimalPlaces(tickSize):Math.max(2,...observed));
 return {type:'price',precision,minMove:hasTick?Math.max(tick,10**-precision):10**-precision};
}
