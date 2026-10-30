import React,{useEffect,useState} from 'react';
import {ShieldCheck} from 'lucide-react';
import {api,setApplicationKey} from '../api.js';
import {Field,ErrorBox} from './Common.jsx';

export default function AuthGate({children}) {
 const [mode,setMode]=useState('checking'),[key,setKey]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let alive=true;api('/health').then(health=>{if(alive)setMode(health.application_key_required?'locked':'open');}).catch(()=>{if(alive)setMode('open');});
  const lock=()=>{setApplicationKey('');setMode('locked');};window.addEventListener('mca:auth-required',lock);
  return()=>{alive=false;window.removeEventListener('mca:auth-required',lock);};},[]);
 if(mode==='open')return children;
 if(mode==='checking')return <div className="auth-page"><p>Connecting to the research workspace…</p></div>;
 async function unlock(event){event.preventDefault();setBusy(true);setError('');setApplicationKey(key);
  try{await api('/api/v1/settings');setKey('');setMode('open');}
  catch(error){setApplicationKey('');setError(error.message);}finally{setBusy(false);}}
 return <main className="auth-page"><section className="panel"><ShieldCheck size={32}/><h1>Unlock your research workspace</h1><p>Enter the application access key configured by the owner. This is not an exchange key.</p><form onSubmit={unlock}><Field label="Application access key"><input type="password" autoComplete="off" required value={key} onChange={e=>setKey(e.target.value)}/></Field><ErrorBox error={error}/><button className="primary" disabled={busy}>{busy?'Checking…':'Unlock workspace'}</button></form><p className="footnote">The key stays in this tab's memory. Reloading or locking the workspace clears it. Exchange trading credentials are never requested.</p></section></main>;
}
