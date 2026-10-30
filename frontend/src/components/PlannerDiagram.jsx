import React from 'react';
import {price} from '../api.js';
import './PlannerDiagram.css';

export default function PlannerDiagram({inputs}){
 if(!inputs)return null;
 const side=inputs.side,entry=Number(inputs.entry),stop=Number(inputs.stop),target=Number(inputs.target);
 if(![entry,stop,target].every(value=>Number.isFinite(value)&&value>0)||
    !(side==='LONG'?stop<entry&&entry<target:side==='SHORT'?target<entry&&entry<stop:false))return null;
 const low=Math.min(entry,stop,target),high=Math.max(entry,stop,target),padding=Math.max((high-low)*0.15,entry*0.005);
 const minimum=low-padding,maximum=high+padding;
 const y=value=>32+(maximum-value)/(maximum-minimum)*216;
 const entryY=y(entry),stopY=y(stop),targetY=y(target);
 const riskTop=Math.min(entryY,stopY),rewardTop=Math.min(entryY,targetY);
 return <div className="planner-diagram"><div className="planner-diagram-heading"><strong>Hypothetical price map · {side}</strong><small>User-supplied reference prices · no live market quote · no order is sent</small></div>
  <svg viewBox="0 0 620 280" role="img" aria-label={`Hypothetical ${side} entry ${price(entry)}, stop ${price(stop)}, take profit ${price(target)}`}>
   <rect x="0" y="0" width="620" height="280" fill="#0b151b"/>
   <rect x="145" y={riskTop} width="330" height={Math.abs(stopY-entryY)} fill="#e87878" fillOpacity="0.19" stroke="#e87878" strokeOpacity="0.7"/>
   <rect x="145" y={rewardTop} width="330" height={Math.abs(targetY-entryY)} fill="#61cda5" fillOpacity="0.19" stroke="#61cda5" strokeOpacity="0.7"/>
   {[[target,targetY,'Take profit','#75dfb9'],[entry,entryY,'Entry','#8ec5e7'],[stop,stopY,'Stop reference','#ef9b9b']].map(([value,position,label,color])=><g key={label}>
    <line x1="90" x2="530" y1={position} y2={position} stroke={color} strokeWidth="2" strokeDasharray={label==='Entry'?'':'6 4'}/>
    <text x="15" y={position-5} fill={color} fontSize="12" fontWeight="600">{label}</text>
    <text x="540" y={position+4} fill={color} fontSize="12" fontWeight="600">{price(value)}</text>
   </g>)}
   <text x="310" y="270" fill="#89a8a6" fontSize="11" textAnchor="middle">Reward and risk are price distances; fills and timing are uncertain.</text>
  </svg>
 </div>;
}
