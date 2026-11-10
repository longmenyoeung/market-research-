import React, { useEffect, useState } from 'react';
import { ArrowUpRight, Calculator, Newspaper, RefreshCw, Sparkles } from 'lucide-react';
import { api, post, localTime, number, price, queryString } from '../api.js';
import { Badge, Busy, Empty, ErrorBox, Field } from './Common.jsx';
import './NewsScanner.css';

export const FOCUSED_COINS = [
  { symbol: 'XAUTUSDT', label: 'មាស / Gold (XAUTUSDT)', short: 'មាស · Gold' },
  { symbol: 'BTCUSDT', label: 'Bitcoin (BTCUSDT)', short: 'Bitcoin · BTC' },
  { symbol: 'ETHUSDT', label: 'Ethereum (ETHUSDT)', short: 'Ethereum · ETH' },
];

export function formatCoinLabel(symbol) {
  const found = FOCUSED_COINS.find(c => c.symbol === symbol);
  return found ? found.label : symbol;
}

const SAMPLE_BULLISH_NEWS = [
  'តម្លៃមាស (Gold XAUT) ឡើងថ្លៃខ្លាំង to record high on safe-haven demand and central bank buying',
  'Bitcoin surges past resistance on record spot BTC ETF inflows and institutional demand',
  'Ethereum rallies strongly as spot ETH ETF inflows jump and layer-2 adoption expands',
].join('\n');

const SAMPLE_BEARISH_NEWS = [
  'Gold drops as strong dollar surges and Fed signals hawkish rate hike outlook',
  'Bitcoin plunges after record ETF outflows and heavy crypto liquidations',
  'Ethereum falls sharply amid selling pressure and exchange outflow concerns',
].join('\n');

export default function NewsScanner({ timeframe, onSelectSymbol, onOpenPlan }) {
  const [scanData, setScanData] = useState(null);
  const [customText, setCustomText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    setBusy(true);
    api(`/api/v1/news/scan?${queryString({ timeframe })}`)
      .then(data => {
        if (alive) setScanData(data);
      })
      .catch(err => {
        if (alive) setError(err.message);
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [timeframe]);

  async function runScan({ fetchLiveRss = true, headlinesText = customText } = {}) {
    setBusy(true);
    setError('');
    const custom_headlines = headlinesText
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean);
    try {
      const data = await post('/api/v1/news/scan', {
        timeframe,
        fetch_live_rss: fetchLiveRss,
        custom_headlines,
      });
      setScanData(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const signals = scanData?.signals || [];
  const newsItems = scanData?.news_items || [];

  return (
    <div className="news-workspace">
      <section className="panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">FOCUSED 3-ASSET NEWS + TECHNICAL SCANNER</span>
            <h3>Analyze News Bot Scan · មាស (Gold) · Bitcoin · ETH</h3>
          </div>
          <Badge>{scanData ? `Scanned ${localTime(scanData.scanned_at)}` : 'Ready'}</Badge>
        </div>
        <p>
          Scans news headlines and combines sentiment with {timeframe} technical indicators (EMA trend, RSI, MACD, ADX, Support/Resistance, and ATR) exclusively for{' '}
          <strong>មាស / Gold (XAUTUSDT)</strong>, <strong>Bitcoin (BTCUSDT)</strong>, and <strong>ETH (ETHUSDT)</strong> to tell you when to <strong>BUY (ទិញ)</strong> and when to <strong>SELL (លក់)</strong>.
        </p>

        <div className="focused-coins-bar" role="group" aria-label="Focused 3 coins">
          {FOCUSED_COINS.map(coin => (
            <button
              key={coin.symbol}
              type="button"
              className="coin-pill"
              onClick={() => onSelectSymbol(coin.symbol)}
            >
              <span>{coin.short}</span>
              <small>{coin.symbol}</small>
              <ArrowUpRight size={14} />
            </button>
          ))}
        </div>

        <div className="news-input-grid">
          <Field
            label="Paste or type news headlines to analyze (English or Khmer — one headline per line)"
            hint="Combine with live RSS feeds or analyze your own breaking news headlines offline."
          >
            <textarea
              aria-label="News headlines to analyze"
              className="news-textarea"
              placeholder="Example: Gold surges on safe-haven buying as Fed signals rate cut&#10;Bitcoin breaks out on strong ETF inflows&#10;Ethereum drops on regulatory concerns"
              value={customText}
              onChange={e => setCustomText(e.target.value)}
            />
          </Field>

          <div className="news-actions-box">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => runScan({ fetchLiveRss: true, headlinesText: customText })}
            >
              <RefreshCw size={15} className={busy ? 'spin' : ''} />
              {busy ? 'Scanning news & chart...' : 'Scan Live News & Analyze BUY/SELL'}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => runScan({ fetchLiveRss: false, headlinesText: customText })}
            >
              <Sparkles size={15} />
              Analyze Entered News Only
            </button>
            <div className="inline">
              <button
                type="button"
                onClick={() => {
                  setCustomText(SAMPLE_BULLISH_NEWS);
                  runScan({ fetchLiveRss: false, headlinesText: SAMPLE_BULLISH_NEWS });
                }}
              >
                Test Bullish News
              </button>
              <button
                type="button"
                onClick={() => {
                  setCustomText(SAMPLE_BEARISH_NEWS);
                  runScan({ fetchLiveRss: false, headlinesText: SAMPLE_BEARISH_NEWS });
                }}
              >
                Test Bearish News
              </button>
            </div>
          </div>
        </div>

        <ErrorBox error={error} />
        {busy && <Busy>Scanning news sentiment and calculating BUY / SELL price levels for មាស (Gold), Bitcoin, and ETH...</Busy>}
        {scanData?.feed_warnings?.length > 0 && (
          <p className="muted">
            Feed status: {scanData.feed_warnings.join(' · ')} (Local technical analysis and custom/saved news remain active.)
          </p>
        )}
      </section>

      <div className="news-signal-grid">
        {signals.map(sig => {
          const actionClass = sig.action.toLowerCase();
          const actionLabel =
            sig.action === 'BUY'
              ? 'BUY · ទិញ (LONG)'
              : sig.action === 'SELL'
              ? 'SELL · លក់ (SHORT)'
              : 'WAIT · រង់ចាំ';
          return (
            <section
              key={sig.symbol}
              className={`panel news-signal-card ${actionClass}`}
              data-testid={`news-card-${sig.symbol}`}
            >
              <div className="signal-card-header">
                <div>
                  <span className="eyebrow">{sig.symbol} · {sig.technical?.timeframe || timeframe}</span>
                  <h3>{sig.name}</h3>
                  <small>{sig.alignment}</small>
                </div>
                <span className={`signal-action-pill ${actionClass}`} data-testid={`signal-action-${sig.symbol}`}>
                  {actionLabel}
                </span>
              </div>

              <p>{sig.decision_summary}</p>

              <div className="signal-metrics-row">
                <div>
                  <span>LAST PRICE ({sig.technical?.data_source === 'live' ? 'LIVE' : 'ARCHIVE'})</span>
                  <strong>{price(sig.technical?.last_price)} USDT</strong>
                </div>
                <div>
                  <span>COMBINED SCORE</span>
                  <strong>{number(sig.combined_score, 1)} / 100</strong>
                </div>
                <div>
                  <span>NEWS SENTIMENT</span>
                  <strong>
                    <Badge>{sig.news?.sentiment}</Badge> ({number(sig.news?.score, 1)})
                  </strong>
                </div>
                <div>
                  <span>TECHNICAL TREND</span>
                  <strong>
                    <Badge>{sig.technical?.trend || 'unknown'}</Badge> · RSI {number(sig.technical?.rsi, 1)}
                  </strong>
                </div>
              </div>

              {sig.buy_plan && (
                <div className="when-box buy-box">
                  <h4>
                    <span>When to BUY (ទិញ / LONG)</span>
                    <Badge tone={sig.action === 'BUY' ? 'positive' : ''}>
                      {sig.action === 'BUY' ? 'ACTIVE BUY' : 'CONDITIONAL'}
                    </Badge>
                  </h4>
                  <p>{sig.buy_plan.when_to_buy}</p>
                  <div className="level-pills">
                    <span>Entry: {price(sig.buy_plan.entry)}</span>
                    <span>SL: {price(sig.buy_plan.stop)}</span>
                    {sig.buy_plan.targets.map((t, i) => (
                      <span key={i}>TP{i + 1}: {price(t)}</span>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => onOpenPlan(sig.buy_plan, sig.symbol)}
                  >
                    <Calculator size={14} /> Plan BUY in Trade Planner
                  </button>
                </div>
              )}

              {sig.sell_plan && (
                <div className="when-box sell-box">
                  <h4>
                    <span>When to SELL (លក់ / SHORT)</span>
                    <Badge tone={sig.action === 'SELL' ? 'negative' : ''}>
                      {sig.action === 'SELL' ? 'ACTIVE SELL' : 'CONDITIONAL'}
                    </Badge>
                  </h4>
                  <p>{sig.sell_plan.when_to_sell}</p>
                  <div className="level-pills">
                    <span>Entry: {price(sig.sell_plan.entry)}</span>
                    <span>SL: {price(sig.sell_plan.stop)}</span>
                    {sig.sell_plan.targets.map((t, i) => (
                      <span key={i}>TP{i + 1}: {price(t)}</span>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => onOpenPlan(sig.sell_plan, sig.symbol)}
                  >
                    <Calculator size={14} /> Plan SELL in Trade Planner
                  </button>
                </div>
              )}

              {sig.news?.top_catalysts?.length > 0 && (
                <div>
                  <small>Top news drivers for {sig.name}:</small>
                  <ul className="catalyst-list">
                    {sig.news.top_catalysts.slice(0, 3).map((cat, idx) => (
                      <li key={idx}>
                        <Badge>{cat.sentiment}</Badge> {cat.title}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <button type="button" onClick={() => onSelectSymbol(sig.symbol)}>
                Inspect {sig.symbol} Chart <ArrowUpRight size={15} />
              </button>
            </section>
          );
        })}
      </div>

      <section className="panel">
        <div className="section-heading">
          <h3>Scanned News Headlines ({newsItems.length})</h3>
          <Badge>មាស (XAUTUSDT) · BTCUSDT · ETHUSDT</Badge>
        </div>
        {newsItems.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Coins Affected</th>
                  <th>Sentiment</th>
                  <th>Score</th>
                  <th>Impact</th>
                  <th>Headline & Analysis Reason</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {newsItems.map((item, idx) => (
                  <tr key={idx}>
                    <td>
                      <div className="inline">
                        {item.matched_assets.map(sym => (
                          <Badge key={sym}>{sym}</Badge>
                        ))}
                      </div>
                    </td>
                    <td>
                      <Badge>{item.sentiment}</Badge>
                    </td>
                    <td>{number(item.score, 0)}</td>
                    <td>
                      <Badge>{item.impact}</Badge>
                    </td>
                    <td>
                      <strong>{item.title}</strong>
                      <small>{item.reason}</small>
                    </td>
                    <td>
                      <span>{item.source}</span>
                      <small>{localTime(item.published_at)}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No news headlines analyzed yet">
            Click "Scan Live News & Analyze BUY/SELL" or paste headlines above to analyze how news impacts មាស (Gold), Bitcoin, and ETH.
          </Empty>
        )}
      </section>
    </div>
  );
}

