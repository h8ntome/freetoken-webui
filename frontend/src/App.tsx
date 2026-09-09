import { useCallback, useEffect, useState } from 'react'
import { Activity, Boxes, Braces, ChevronLeft, Cpu, Gauge, Menu, MessageSquare, Moon, ScrollText, Settings as SettingsIcon, Sun } from 'lucide-react'
import { api, post, setCsrf } from './lib/api'
import type { EngineStatus, Metrics, Model } from './types'
import Dashboard from './pages/Dashboard'
import Chat from './pages/Chat'
import Models from './pages/Models'
import Performance from './pages/Performance'
import ApiPage from './pages/ApiPage'
import Logs from './pages/Logs'
import Settings from './pages/Settings'
import Login from './pages/Login'

const pages = [
  ['overview','Overview',Gauge], ['chat','Chat',MessageSquare], ['models','Models',Boxes],
  ['performance','Performance',Activity], ['api','API',Braces], ['logs','Logs',ScrollText], ['settings','Settings',SettingsIcon]
] as const

export default function App() {
  const [page,setPage]=useState(()=>location.hash.slice(1)||'overview')
  const [me,setMe]=useState<{username:string;csrfToken:string;authEnabled:boolean}|null>(null)
  const [authChecked,setAuthChecked]=useState(false)
  const [engine,setEngine]=useState<EngineStatus>({state:'stopped',mode:'managed',owned:true})
  const [models,setModels]=useState<Model[]>([])
  const [metrics,setMetrics]=useState<Metrics|null>(null)
  const [sidebar,setSidebar]=useState(false)
  const [theme,setTheme]=useState(()=>localStorage.getItem('ftw-theme')||'dark')
  const [notice,setNotice]=useState<{text:string;bad?:boolean}|null>(null)

  const toast=useCallback((text:string,bad=false)=>{setNotice({text,bad});setTimeout(()=>setNotice(null),3500)},[])
  const refresh=useCallback(async()=>{
    try {
      const [e,m,mt]=await Promise.all([api<EngineStatus>('/api/engine/status'),api<{items:Model[]}>('/api/models'),api<Metrics>('/api/metrics')])
      setEngine(e);setModels(m.items);setMetrics(mt)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to reach the WebUI backend'
      setEngine(current => ({...current, state: 'failed', error: message}))
    }
  },[])
  useEffect(()=>{api<any>('/api/auth/me').then(v=>{setMe(v);setCsrf(v.csrfToken)}).catch(()=>setMe(null)).finally(()=>setAuthChecked(true))},[])
  useEffect(()=>{if(!me)return;refresh();const id=setInterval(refresh,2500);return()=>clearInterval(id)},[me,refresh])
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('ftw-theme',theme)},[theme])
  useEffect(()=>{const onHash=()=>setPage(location.hash.slice(1)||'overview');window.addEventListener('hashchange',onHash);return()=>window.removeEventListener('hashchange',onHash)},[])
  const navigate=(id:string)=>{location.hash=id;setSidebar(false)}
  if(!authChecked) return <div className="app-loading"><span className="mark">F</span><p>Opening control plane…</p></div>
  if(!me) return <Login onLogin={v=>{setMe(v);setCsrf(v.csrfToken)}} />
  const shared={engine,models,metrics,refresh,toast,navigate}
  return <div className="shell">
    <aside className={sidebar?'sidebar open':'sidebar'}>
      <div className="brand"><span className="mark">F</span><div><strong>FreeToken</strong><small>WEB CONTROL PLANE</small></div><button className="mobile-close" onClick={()=>setSidebar(false)}><ChevronLeft/></button></div>
      <nav>{pages.map(([id,label,Icon])=><button key={id} onClick={()=>navigate(id)} className={page===id?'active':''}><Icon size={18}/><span>{label}</span>{id==='models'&&models.length>0&&<em>{models.length}</em>}</button>)}</nav>
      <div className="sidebar-foot"><div className="engine-mini"><span className={`pulse ${engine.state}`} /><div><small>ENGINE</small><strong>{engine.state==='ready'?engine.model:engine.state}</strong></div></div><button className="theme" onClick={()=>setTheme(theme==='dark'?'light':'dark')} aria-label="Toggle theme">{theme==='dark'?<Sun size={17}/>:<Moon size={17}/>}</button></div>
    </aside>
    <main><header className="topbar"><button className="menu" onClick={()=>setSidebar(true)}><Menu/></button><div><span>FT / </span>{pages.find(x=>x[0]===page)?.[1]||'Overview'}</div><div className="runtime-pill"><Cpu size={15}/><span>{metrics?.system.gpus[0]?.name||'GPU unavailable'}</span><i className={engine.state}/></div></header>
      <div className="page-wrap">
        {page==='overview'&&<Dashboard {...shared}/>} {page==='chat'&&<Chat {...shared}/>} {page==='models'&&<Models {...shared}/>} {page==='performance'&&<Performance {...shared}/>} {page==='api'&&<ApiPage {...shared}/>} {page==='logs'&&<Logs {...shared}/>} {page==='settings'&&<Settings {...shared} me={me}/>} 
      </div>
    </main>
    {notice&&<div className={notice.bad?'toast bad':'toast'}>{notice.bad?'!':'✓'} {notice.text}</div>}
  </div>
}
