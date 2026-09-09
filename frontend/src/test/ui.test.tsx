import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Dashboard from '../pages/Dashboard'
import Performance from '../pages/Performance'
import Models from '../pages/Models'

const base:any={engine:{state:'stopped',mode:'managed',owned:true},models:[],metrics:null,refresh:vi.fn(),toast:vi.fn(),navigate:vi.fn()}

describe('core states',()=>{
  it('renders useful onboarding with no model',()=>{render(<Dashboard {...base}/>);expect(screen.getByText('Your library is waiting')).toBeInTheDocument();expect(screen.getByRole('button',{name:/Browse verified models/i})).toBeEnabled()})
  it('renders a real engine error',()=>{render(<Dashboard {...base} models={[{id:'m',name:'m',path:'/m',architecture:'Qwen',sizeBytes:1,status:'downloaded',compatibility:'likely',modifiedAt:1}]} engine={{...base.engine,state:'failed',error:'CUDA driver mismatch'}}/>);expect(screen.getByText('CUDA driver mismatch')).toBeInTheDocument()})
  it('does not invent performance samples',()=>{render(<Performance engine={base.engine} metrics={null}/>);expect(screen.getByText('Collecting live samples…')).toBeInTheDocument();expect(screen.getByText('NVML unavailable')).toBeInTheDocument()})
  it('hides local model controls in external mode',()=>{render(<Models {...base} engine={{state:'external',mode:'external',owned:false,health:{status:'ok'}}}/>);expect(screen.getByText('Model controls live on the external server')).toBeInTheDocument();expect(screen.queryByRole('button',{name:/Download/i})).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:/Load/i})).not.toBeInTheDocument()})
})
