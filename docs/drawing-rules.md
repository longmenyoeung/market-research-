# Deterministic chart drawing rules

Every annotation contains `id`, `kind`, `label`, source `timeframe`, price/time
`points`, `rule`, `confirmed` and `confirmed_at` (UTC milliseconds). A confirmed
observation means its input candles were closed; it does not confirm a future
price outcome. Candle times on the chart are opening times. `confirmed_at` records
when the observation became knowable. Settings and rules accompany saved reports.

| Drawing | Calculation and confirmation |
| --- | --- |
| Swing high/low | Unique maximum high/minimum low in `2*w+1` closed candles; default w=3. The pivot is known at the close of the w-th right candle. Ties within the window produce no pivot. |
| HH/LH/HL/LL | Compare each confirmed pivot with the previous pivot of the same type. Exact equal highs/lows are EH/EL. Latest two highs and lows must both rise/fall to classify bullish/bearish structure; mixed/equal structure is neutral. |
| BOS | A closed candle exceeds an already confirmed, unbroken swing high or falls below its swing low. A pivot becomes active for event detection on the following bar. A level breaks only once. |
| CHoCH | A confirmed break reverses the direction of the preceding detected break. This is a deterministic local convention, not proof of a lasting reversal. |
| Potential sweep | Wick exceeds an active confirmed swing and closes back inside. The observation is provisional because actual resting liquidity and trader intent are unknown. |
| Retest | Within 10 bars after a break, a later candle range revisits the level within +/-0.25 of that bar's ATR and closes on the breakout side. Current ATR is never applied retroactively to historical events. |
| Fair Value Gap | Bullish: low[t] > high[t-2]. Bearish: high[t] < low[t-2]. The three-bar non-overlap is observed only when t closes. It predicts neither a fill nor continuation. |
| FVG fill status | A later bullish-gap bar reaches the lower boundary, or bearish-gap bar reaches the upper boundary. `filled_as_of_latest_bar` and `filled_at` describe later observations; they are never presented as known at gap creation. Partial penetration is not a full fill. |
| Support/resistance zones | Nearest confirmed swing below/above the latest close, drawn from the most recent 40 confirmed swings. Half-width is 0.25 of current ATR. Zones describe the current snapshot and may move as new swings confirm. |
| Trendlines | Join the two latest confirmed highs, or the two latest confirmed lows. Extrapolation is a visual reference, not a prediction. |
| Price channel | Fit OLS to the last 20 closes; upper/lower lines use extreme high/low residual offsets over those 20 bars. |
| Fibonacci | Latest confirmed pivot and most recent opposite pivot define a leg A->B. Retracements are B-(B-A)*r for r=0.236/0.382/0.5/0.618/0.786. Extensions are A+(B-A)*r for r=1.272/1.618. Nonpositive levels are omitted. |
| Prior day | The previous complete UTC daily candle's high and low. Omitted without the actual daily candle. |
| Floor pivots | P=(H+L+C)/3; R1=2P-L; S1=2P-H; R2=P+H-L; S2=P-H+L, using the previous complete UTC day. Nonpositive levels are omitted. |
| Prior week | Maximum high/minimum low of seven complete daily bars in the previous Monday-Sunday UTC week. Omitted if the complete week is unavailable. Source timeframe is 1d. |
| RSI divergence | Bullish: two confirmed lows decline while RSI at those pivots rises. Bearish: two confirmed highs rise while RSI falls. Uses the configured momentum RSI. Reversal interpretation stays provisional. |
| Candlestick patterns | Latest closed candle: doji body <=10% range; engulfing body encloses opposite prior body; hammer lower shadow >=2 bodies and upper shadow <=1 body. Descriptive shapes do not create guaranteed signals. |
| Entry/stop/targets | A valid LONG references actual best ask, SHORT actual best bid. Entry region is +/-0.1 ATR. Stop is beyond support/resistance with 0.25 ATR buffer, or at least 1.5 ATR from entry. Targets project 1R/2R/3R; projected prices are explicitly hypothetical. Nonpositive scenarios are omitted. |
| Risk/reward area | Under the plan group, shaded price areas connect entry to stop and entry to the 2R target over the last 30 observed bars. The horizontal width has no forecast or holding-time meaning. |

Indicators and structural analysis use only confirmed candles. The current candle
can update visually without generating a confirmed event. Source expiry disables
current candidate classifications. Closed stream events invalidate that symbol's
cached report; known stream losses trigger a selected-market REST refresh. No
whole-universe recalculation occurs on every tick.

Structure rules are implemented in `backend/app/analysis/structure.py`; hypothetical
plan annotations are added by `backend/app/services/platform.py`. Parameters remain
experimental. Drawings are useful for inspection and replay, not proof of edge.
