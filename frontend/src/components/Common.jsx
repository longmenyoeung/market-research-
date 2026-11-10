import React from 'react';
import { AlertTriangle, CircleHelp, LoaderCircle } from 'lucide-react';
export function Badge({children,tone='',title}) { const text=String(children??'unknown'); const color=tone || (text.includes('LONG')||text.includes('BUY')||text==='bullish'||text==='BULLISH'||text==='usable'||text==='normal'?'positive':text.includes('SHORT')||text.includes('SELL')||text==='bearish'||text==='BEARISH'||text==='error'||text==='stale'?'negative':text.includes('NO TRADE')||text.includes('WAIT')||text.includes('warning')?'warning':''); return <span className={`badge ${color}`} title={title}>{children??'unknown'}</span>; }
export function ErrorBox({error}) { return error ? <div className="error-box" role="alert"><AlertTriangle size={18}/><span>{String(error)}</span></div>:null; }
export function Empty({title='No data yet',children}) { return <div className="empty"><CircleHelp size={28}/><h3>{title}</h3><p>{children||'Refresh the market data to begin. Unavailable values are never replaced with sample prices.'}</p></div>; }
export function Busy({children='Loading market data…'}) { return <div className="busy" role="status"><LoaderCircle size={18} className="spin"/>{children}</div>; }
export function Field({label,children,hint}) { return <label className="field"><span>{label}</span>{children}{hint&&<small>{hint}</small>}</label>; }
export function Stat({label,value,sub,accent}) { return <div className={`stat ${accent||''}`}><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>; }
export function List({items}) { return items?.length?<ul className="notes">{items.map((item,i)=><li key={i}>{item}</li>)}</ul>:<p className="muted">None identified in the available data.</p>; }
