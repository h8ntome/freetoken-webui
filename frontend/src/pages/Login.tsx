import { useState } from 'react'
import { ArrowRight, LockKeyhole } from 'lucide-react'
import { post } from '../lib/api'

export default function Login({onLogin}:{onLogin:(v:any)=>void}) {
  const [username,setUsername]=useState('admin'),[password,setPassword]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  return <main className="login-page"><div className="login-orbit"><span/><span/><span/></div><section className="login-panel"><div className="brand big"><span className="mark">F</span><div><strong>FreeToken</strong><small>WEB CONTROL PLANE</small></div></div><div className="eyebrow">SECURE OPERATOR ACCESS</div><h1>Your models.<br/><em>Under control.</em></h1><p>Manage the complete local inference lifecycle from one private workspace.</p><form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');try{onLogin(await post('/api/auth/login',{username,password}))}catch(e:any){setError(e.message)}finally{setBusy(false)}}}><label>Username<input value={username} onChange={e=>setUsername(e.target.value)} autoComplete="username"/></label><label>Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete="current-password" autoFocus/></label>{error&&<div className="form-error">{error}</div>}<button className="primary wide" disabled={busy}><LockKeyhole size={17}/>{busy?'Authenticating…':'Enter control plane'}<ArrowRight size={17}/></button></form><small className="local-note">Credentials are verified by your own server. Nothing leaves this machine.</small></section></main>
}
