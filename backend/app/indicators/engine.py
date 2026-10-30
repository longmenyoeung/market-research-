"""Causal indicator calculations. NaN warm-ups stay unavailable, never filled."""
import math

import numpy as np
import pandas as pd
from ta.momentum import StochasticOscillator, WilliamsRIndicator, AwesomeOscillatorIndicator
from ta.trend import CCIIndicator, PSARIndicator, MACD
from ta.volume import MFIIndicator, ChaikinMoneyFlowIndicator

from app.schemas.candles import Candle, INTERVAL_MS
from app.schemas.platform import IndicatorSettings


def wilder(series: pd.Series, period: int) -> pd.Series:
    """Seed with the first n finite observations' mean, then Wilder recurrence."""
    result = np.full(len(series), np.nan)
    values = series.to_numpy(dtype=float)
    seed: list[float] = []
    previous = math.nan
    for i, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(value)
            if len(seed) == period:
                previous = float(np.mean(seed))
        else:
            previous = (previous * (period - 1) + value) / period
        result[i] = previous
    return pd.Series(result, index=series.index)


def frame(candles: list[Candle], *, closed_only: bool = True) -> pd.DataFrame:
    rows = [{"time": c.open_time, **{key: float(getattr(c, key)) for key in
             ("open", "high", "low", "close", "volume", "quote_volume")}}
            for c in candles if c.is_closed or not closed_only]
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume", "quote_volume"])


def calculate(data: pd.DataFrame, config: IndicatorSettings, timeframe: str) -> tuple[pd.DataFrame, dict]:
    df = data.copy().reset_index(drop=True)
    catalog: dict[str, dict] = {}
    if df.empty:
        return df, catalog
    close, high, low, volume = (df[name] for name in ("close", "high", "low", "volume"))

    def add(name: str, values: pd.Series, group: str, formula: str, warmup: int,
            parameters: dict, interpretation: str, inputs: str = "OHLCV") -> None:
        series = values.astype(float).replace([np.inf, -np.inf], np.nan).copy()
        series.iloc[:max(0, warmup - 1)] = np.nan
        df[name] = series
        catalog[name] = {"group": group, "formula": formula, "warmup": warmup,
                         "parameters": parameters, "inputs": inputs, "interpretation": interpretation}

    for period in sorted(set(config.ema_periods + [config.trend_fast, config.trend_slow, config.trend_anchor])):
        add(f"ema_{period}", close.ewm(span=period, adjust=False, min_periods=period).mean(), "trend",
            "EMA_t = alpha*C_t + (1-alpha)*EMA_(t-1); alpha=2/(n+1), first-close seed",
            period, {"period": period}, "Price above a rising average supports bullish trend; below supports bearish trend.", "close")
    for period in sorted(set(config.sma_periods)):
        add(f"sma_{period}", close.rolling(period).mean(), "trend", "mean(last n closes)", period,
            {"period": period}, "A smoothing reference; correlated with EMA evidence.", "close")
    changes = close.diff()
    for period in sorted(set(config.rsi_periods + [config.momentum_period])):
        gain, loss = wilder(changes.clip(lower=0), period), wilder(-changes.clip(upper=0), period)
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
        add(f"rsi_{period}", rsi, "momentum", "100-100/(1+Wilder(gain,n)/Wilder(loss,n)); flat=50",
            period + 1, {"period": period}, "Above/below 50 indicates momentum; 70/30 are stretched conditions, not automatic reversals.", "close")
    macd = MACD(close, config.macd_slow, config.macd_fast, config.macd_signal, fillna=False)
    for name, values in [("macd", macd.macd()), ("macd_signal", macd.macd_signal()), ("macd_hist", macd.macd_diff())]:
        add(name, values, "momentum", "MACD=EMA(fast)-EMA(slow); signal=EMA(MACD,signal); histogram=MACD-signal",
            config.macd_slow + config.macd_signal - 1, {"fast": config.macd_fast, "slow": config.macd_slow, "signal": config.macd_signal},
            "Positive histogram supports bullish momentum; negative supports bearish; overlaps RSI.", "close")
    true_range = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(true_range, config.atr_period)
    add("atr", atr, "volatility", "Wilder(max(H-L, abs(H-prevC), abs(L-prevC)),n)", config.atr_period,
        {"period": config.atr_period}, "Price-unit movement estimate; not a guaranteed stop distance.")
    add("atr_percent", 100*atr/close, "volatility", "100*ATR/close", config.atr_period,
        {"period": config.atr_period}, "Compares movement scale across instruments.")
    up, down = high.diff(), -low.diff()
    plus_dm, minus_dm = up.where((up > down) & (up > 0), 0.0), down.where((down > up) & (down > 0), 0.0)
    plus, minus = 100*wilder(plus_dm, config.adx_period)/wilder(true_range, config.adx_period), 100*wilder(minus_dm, config.adx_period)/wilder(true_range, config.adx_period)
    dx = (100*(plus-minus).abs()/(plus+minus).replace(0, np.nan)).mask((plus+minus) == 0, 0)
    for name, series, warm in [("di_plus", plus, config.adx_period), ("di_minus", minus, config.adx_period), ("adx", wilder(dx, config.adx_period), 2*config.adx_period-1)]:
        add(name, series, "trend", "DI=100*Wilder(directional movement)/Wilder(TR); ADX=Wilder(100*abs(DI+-DI-)/(DI++DI-))",
            warm, {"period": config.adx_period}, "ADX measures strength, not direction; above 25 is an experimental trend threshold.")
    p = config.momentum_period
    rsi = df[f"rsi_{p}"]
    stoch_rsi = 100*(rsi-rsi.rolling(p).min())/(rsi.rolling(p).max()-rsi.rolling(p).min()).replace(0, np.nan)
    stoch = StochasticOscillator(high, low, close, window=p, smooth_window=config.stochastic_smooth)
    for name, values, formula, warm in [
        ("stoch_rsi", stoch_rsi, "100*(RSI-min(RSI,n))/(max(RSI,n)-min(RSI,n)); zero range unavailable", 2*p),
        ("stochastic_k", stoch.stoch(), "100*(C-lowestLow(n))/(highestHigh(n)-lowestLow(n))", p),
        ("stochastic_d", stoch.stoch_signal(), "SMA(stochastic K,smoothing period)", p+config.stochastic_smooth-1),
        ("cci", CCIIndicator(high, low, close, window=p).cci(), "(typicalPrice-SMA(typicalPrice,n))/(0.015*meanAbsoluteDeviation)", p),
        ("williams_r", WilliamsRIndicator(high, low, close, lbp=p).williams_r(), "-100*(highestHigh(n)-C)/(highestHigh(n)-lowestLow(n))", p),
        ("roc", close.pct_change(p)*100, "100*(C/C[n bars ago]-1)", p+1),
        ("momentum", close-close.shift(p), "C-C[n bars ago]", p+1),
        ("awesome", AwesomeOscillatorIndicator(high, low,window1=config.awesome_fast,window2=config.awesome_slow).awesome_oscillator(), "SMA((H+L)/2,fast)-SMA((H+L)/2,slow)", config.awesome_slow),
    ]:
        add(name, values, "momentum", formula, warm, {"period": p,"smoothing":config.stochastic_smooth} if name != "awesome" else {"fast": config.awesome_fast, "slow": config.awesome_slow},
            "Momentum context; correlated oscillators count as one evidence group.")
    mid, std = close.rolling(config.band_period).mean(), close.rolling(config.band_period).std(ddof=0)
    for name, series in [("bb_mid", mid), ("bb_upper", mid+config.band_multiplier*std), ("bb_lower", mid-config.band_multiplier*std)]:
        add(name, series, "volatility", "SMA(C,n) +/- multiplier*populationStd(C,n)", config.band_period,
            {"period": config.band_period, "multiplier": config.band_multiplier}, "Band contact alone is not an entry signal.", "close")
    channel_mid = close.ewm(span=config.channel_period, adjust=False, min_periods=config.channel_period).mean()
    for name, series in [("keltner_mid", channel_mid), ("keltner_upper", channel_mid+config.band_multiplier*atr),
                         ("keltner_lower", channel_mid-config.band_multiplier*atr),
                         ("donchian_upper", high.rolling(config.channel_period).max()), ("donchian_lower", low.rolling(config.channel_period).min())]:
        add(name, series, "volatility", "Keltner=EMA(C,n)+/-m*ATR; Donchian=rolling high/low including current bar",
            max(config.channel_period, config.atr_period), {"period": config.channel_period, "multiplier": config.band_multiplier},
            "Range/channel context; breakout comparison must use the previous channel.")
    log_returns = np.log(close/close.shift())
    vol = log_returns.rolling(config.volatility_period).std(ddof=1)*np.sqrt(365*86400000/INTERVAL_MS[timeframe])*100
    add("realized_volatility", vol, "volatility", "sampleStd(log returns,n)*sqrt(365*barsPerDay)*100",
        config.volatility_period+1, {"period": config.volatility_period}, "Annualized historical volatility; not a forecast.", "close")
    percentile = vol.rolling(config.percentile_period).apply(lambda a: 100*(np.mean(a < a[-1])+0.5*np.mean(a == a[-1])), raw=True)
    add("volatility_percentile", percentile, "volatility", "100*(fraction of trailing vol samples below latest + half the tied fraction)",
        config.volatility_period+config.percentile_period, {"period": config.percentile_period}, "Below 20 compressed; above 80 elevated.", "close")
    vol_ma = volume.rolling(config.volume_period).mean()
    obv = (np.sign(close.diff()).fillna(0)*volume).cumsum()
    for name, values, formula, warm in [
        ("volume_ma", vol_ma, "SMA(volume,n)", config.volume_period),
        ("relative_volume", volume/vol_ma.replace(0, np.nan), "volume/SMA(volume,n)", config.volume_period),
        ("obv", obv, "cumulative(sign(C-prevC)*volume), initial=0", 2),
        ("cmf", ChaikinMoneyFlowIndicator(high, low, close, volume, window=config.volume_period).chaikin_money_flow(), "sum(((2C-H-L)/(H-L))*V,n)/sum(V,n); flat bars contribute zero", config.volume_period),
        ("mfi", MFIIndicator(high, low, close, volume, window=p).money_flow_index(), "100-100/(1+positiveTypicalPriceFlow/negativeFlow)", p),
    ]:
        add(name, values, "volume", formula, warm, {"period": p if name == "mfi" else config.volume_period},
            "Volume participation context; exchange-specific, not independent of price direction.")
    typical = (high+low+close)/3
    day = pd.to_datetime(df.time, unit="ms", utc=True).dt.floor("D")
    vwap = (typical*volume).groupby(day).cumsum()/volume.groupby(day).cumsum().replace(0, np.nan)
    add("vwap", vwap, "volume", "cumulative(typicalPrice*V)/cumulative(V), reset at UTC midnight",
        1, {"anchor": "UTC day"}, "Session value reference; first loaded day may be partial.")
    slope = close.rolling(config.regression_period).apply(lambda a: np.polyfit(np.arange(len(a)), a, 1)[0], raw=True)
    add("regression_slope", slope, "trend", "OLS slope of last n closes versus bar index", config.regression_period,
        {"period": config.regression_period}, "Positive/negative price-unit slope; correlated with averages.", "close")
    conversion = (high.rolling(config.ichimoku_fast).max()+low.rolling(config.ichimoku_fast).min())/2
    base = (high.rolling(config.ichimoku_base).max()+low.rolling(config.ichimoku_base).min())/2
    span_b = (high.rolling(config.ichimoku_span).max()+low.rolling(config.ichimoku_span).min())/2
    for name, values, warm in [("ichimoku_conversion", conversion, config.ichimoku_fast), ("ichimoku_base", base, config.ichimoku_base),
                              ("ichimoku_a", ((conversion+base)/2).shift(config.ichimoku_base), 2*config.ichimoku_base),
                              ("ichimoku_b", span_b.shift(config.ichimoku_base), config.ichimoku_span+config.ichimoku_base)]:
        add(name, values, "trend", "Conversion/base=midrange(n); cloud A=mean(conversion,base), B=midrange(span), cloud shifted forward base bars",
            warm, {"fast": config.ichimoku_fast, "base": config.ichimoku_base, "span": config.ichimoku_span}, "Current cloud uses past data only; no future-filled lagging series.")
    sar = PSARIndicator(high, low, close, step=config.sar_step, max_step=config.sar_max).psar()
    add("psar", sar, "trend", "SAR_next=SAR+acceleration*(extreme-SAR), clamped to previous two extrema; reversal resets",
        3, {"step": config.sar_step, "maximum": config.sar_max}, "Trailing trend reference that can whipsaw in ranges.")
    upper, lower = ((high+low)/2+config.supertrend_multiplier*atr).to_numpy(), ((high+low)/2-config.supertrend_multiplier*atr).to_numpy()
    direction = np.ones(len(df)); supertrend = np.full(len(df), np.nan)
    for i in range(config.atr_period-1, len(df)):
        if i > config.atr_period-1:
            if close.iloc[i-1] <= upper[i-1]: upper[i] = min(upper[i], upper[i-1])
            if close.iloc[i-1] >= lower[i-1]: lower[i] = max(lower[i], lower[i-1])
            direction[i] = 1 if close.iloc[i] > upper[i-1] else -1 if close.iloc[i] < lower[i-1] else direction[i-1]
        supertrend[i] = lower[i] if direction[i] == 1 else upper[i]
    add("supertrend", pd.Series(supertrend), "trend", "HL2 +/- multiplier*ATR trailing bands; close beyond prior opposite band flips trend",
        config.atr_period, {"multiplier": config.supertrend_multiplier, "atr_period": config.atr_period}, "Trailing volatility-adjusted reference; initialization begins bullish.")
    return df, catalog


def finite(value) -> float | None:
    return float(value) if value is not None and np.isfinite(value) else None


def snapshot(df: pd.DataFrame, catalog: dict) -> dict:
    if df.empty:
        return {}
    return {name: {"value": finite(df[name].iloc[-1]), **meta} for name, meta in catalog.items()}


def series_payload(df: pd.DataFrame, catalog: dict) -> dict:
    return {name: [{"time": int(t/1000), "value": float(v)} for t, v in zip(df.time, df[name]) if np.isfinite(v)] for name in catalog}
