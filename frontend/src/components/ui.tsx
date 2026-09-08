import type { ReactNode } from 'react'
import { Check, Copy, X } from 'lucide-react'
import { useEffect, useState } from 'react'

export function Badge({children, tone='neutral'}:{children:ReactNode,tone?:'good'|'warn'|'bad'|'neutral'|'accent'}) {
  return <span className={`badge badge-${tone}`}><i />{children}</span>
}

export function Empty({icon, title, children, action}:{icon:ReactNode,title:string,children:ReactNode,action?:ReactNode}) {
  return <div className="empty"><div className="empty-icon">{icon}</div><h3>{title}</h3><p>{children}</p>{action}</div>
}

export function Modal({title, children, onClose}:{title:string,children:ReactNode,onClose:()=>void}) {
  useEffect(()=>{ const key=(e:KeyboardEvent)=>e.key==='Escape'&&onClose(); window.addEventListener('keydown',key); return()=>window.removeEventListener('keydown',key)},[onClose])
  return <div className="modal-backdrop" role="presentation" onMouseDown={e=>e.target===e.currentTarget&&onClose()}><section className="modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18}/></button></header>{children}</section></div>
}

export function CopyButton({value, label='Copy'}:{value:string,label?:string}) {
  const [done,setDone]=useState(false)
  return <button className="copy-button" onClick={async()=>{await navigator.clipboard.writeText(value);setDone(true);setTimeout(()=>setDone(false),1200)}}>{done?<Check size={14}/>:<Copy size={14}/>} {done?'Copied':label}</button>
}

export function Meter({value, tone='accent'}:{value:number,tone?:'accent'|'green'|'orange'}) {
  return <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}><i className={tone} style={{width:`${Math.min(100,Math.max(0,value))}%`}} /></div>
}

export function Skeleton({lines=3}:{lines?:number}) { return <div className="skeleton">{Array.from({length:lines},(_,i)=><i key={i} style={{width:`${95-i*11}%`}} />)}</div> }

