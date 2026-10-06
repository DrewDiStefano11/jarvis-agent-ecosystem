import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'
import { Approvals } from '../src/pages/Approvals'
import { useAppStore } from '../src/state/AppStore'
import { ApiError } from '../src/api/client'
import type { Approval, SystemStatus } from '../src/types/contracts'
vi.mock('../src/state/AppStore',()=>({useAppStore:vi.fn()}))
const approval={id:'a1',taskId:'t1',requestedByAgentId:'s1',actionType:'simulated_publish',title:'Publish report',description:'Simulated report',reason:'Operator review',riskLevel:'yellow',affectedResources:['demo'],exactActionPreview:'<script>unsafe()</script>',expectedOutcome:'Demo artifact',reversalMethod:'Reset demo',expiresAt:'2039-01-01T00:00:00Z',status:'pending',reviewedBy:null,reviewedAt:null,decisionNote:null,createdAt:'2026-01-01T00:00:00Z'} as Approval
let store:ReturnType<typeof useAppStore>
const show=()=>render(<MemoryRouter><Approvals/></MemoryRouter>)
beforeEach(()=>{
 store={approvals:[approval],agents:[{id:'s1',name:'Scout'}],tasks:[{id:'t1',title:'Research trip'}],system:{emergencyStop:false},connection:'connected',error:null,resyncRequired:false,loading:false,lastSync:'initial',action:vi.fn().mockResolvedValue({}),refresh:vi.fn(),selectTask:vi.fn()} as unknown as typeof store
 vi.mocked(useAppStore).mockImplementation(()=>store)
 vi.spyOn(window,'confirm').mockReturnValue(false)
})
test('shows exact escaped preview, real names and explicit authority scope',()=>{
 show();expect(screen.getByText('<script>unsafe()</script>')).toBeInTheDocument();expect(screen.getByText(/Agent: Scout/)).toHaveTextContent('Research trip');expect(screen.getByText(/does not authorize native workspace/)).toBeInTheDocument();expect(screen.queryByText(/in-memory/)).not.toBeInTheDocument()
})
test('filters shared records without inventing empty history',async()=>{
 store.approvals=[approval,{...approval,id:'a2',title:'Older refusal',status:'rejected',riskLevel:'red'}];show()
 await userEvent.selectOptions(screen.getByLabelText('Status'),'rejected');expect(screen.getByText('1 of 2 records shown')).toBeInTheDocument();expect(screen.queryByRole('article',{name:'Publish report'})).not.toBeInTheDocument()
 await userEvent.type(screen.getByRole('searchbox'),'absent');expect(screen.getByText('No approvals match these filters.')).toBeInTheDocument()
})
test('optional note is sent exactly once while pending and ack survives filtering',async()=>{
 let finish:()=>void=()=>{};store.action=vi.fn(()=>new Promise<void>(resolve=>{finish=resolve})) as typeof store.action
 show();await userEvent.type(screen.getByRole('textbox',{name:'Decision note for Publish report'}),' Reviewed scope ')
 fireEvent.click(screen.getByRole('button',{name:'Approve'}));fireEvent.click(screen.getByRole('button',{name:'Reject'}));expect(store.action).toHaveBeenCalledExactlyOnceWith('/api/approvals/a1/approve',{decisionNote:'Reviewed scope'})
 expect(screen.getByRole('button',{name:'Reject'})).toBeDisabled();await userEvent.type(screen.getByRole('searchbox'),'absent');await act(async()=>finish());expect(screen.getByRole('status')).toHaveTextContent('Approval approved.')
})
test('stale, missing and emergency state disable both decisions',()=>{
 store.error='Unavailable';const view=show();expect(screen.getByRole('button',{name:'Approve'})).toBeDisabled();expect(screen.getByRole('button',{name:'Reject'})).toBeDisabled()
 store.error=null;store.system=null;view.rerender(<MemoryRouter><Approvals/></MemoryRouter>);expect(screen.getByText(/System state is unavailable/)).toBeInTheDocument()
 store.system={emergencyStop:true} as SystemStatus;view.rerender(<MemoryRouter><Approvals/></MemoryRouter>);expect(screen.getByText(/Emergency stop is active/)).toBeInTheDocument();expect(screen.getByRole('button',{name:'Reject'})).toBeDisabled()
})
test('expired is blocked and black risk still permits refusal',()=>{
 store.approvals=[{...approval,id:'e',title:'Expired',expiresAt:'2020-01-01T00:00:00Z'},{...approval,id:'b',title:'Prohibited',riskLevel:'black'}];show()
 const old=within(screen.getByRole('article',{name:'Expired'}));expect(old.getByRole('button',{name:'Approve'})).toBeDisabled();expect(old.getByRole('button',{name:'Reject'})).toBeDisabled()
 const black=within(screen.getByRole('article',{name:'Prohibited'}));expect(black.getByRole('button',{name:'Approve'})).toBeDisabled();expect(black.getByRole('button',{name:'Reject'})).toBeEnabled()
})
test('expiry crossing after render is checked again before sending',()=>{
 const expires=new Date(Date.now()+10000).toISOString();store.approvals=[{...approval,expiresAt:expires}];show();vi.spyOn(Date,'now').mockReturnValue(Date.parse(expires)+1);fireEvent.click(screen.getByRole('button',{name:'Approve'}));expect(store.action).not.toHaveBeenCalled()
})
test('red confirmation shows exact proposal and decline sends nothing',async()=>{
 store.approvals=[{...approval,riskLevel:'red'}];show();await userEvent.click(screen.getByRole('button',{name:'Approve'}));expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('<script>unsafe()</script>'));expect(store.action).not.toHaveBeenCalled()
 vi.mocked(window.confirm).mockReturnValue(true);await userEvent.click(screen.getByRole('button',{name:'Approve'}));expect(store.action).toHaveBeenCalledOnce()
})
test('lost acknowledgement blocks replay until successful snapshot changes',async()=>{
 store.action=vi.fn().mockRejectedValue(new TypeError('Network'));const view=show();await userEvent.click(screen.getByRole('button',{name:'Approve'}));expect(screen.getByRole('alert')).toHaveTextContent('may have recorded it');expect(screen.getByRole('button',{name:'Approve'})).toBeDisabled();expect(store.action).toHaveBeenCalledOnce()
 store.lastSync='refreshed';store.approvals=[{...approval,status:'approved',decisionNote:'persisted note',reviewedBy:'operator'}];view.rerender(<MemoryRouter><Approvals/></MemoryRouter>);expect(screen.getByRole('button',{name:'Approve'})).toBeDisabled();await userEvent.click(screen.getByText('Reviewed decision'));expect(screen.getByText('persisted note')).toBeVisible()
})
test('backend rejection exposes error code without success claim',async()=>{
 store.action=vi.fn().mockRejectedValue(new ApiError('APPROVAL_EXPIRED','Expired approvals cannot be processed.',409));show();await userEvent.click(screen.getByRole('button',{name:'Approve'}));expect(screen.getByRole('alert')).toHaveTextContent('APPROVAL_EXPIRED');expect(screen.queryByText(/request acknowledged/)).not.toBeInTheDocument()
})

test('refusal acknowledges the actual rejected decision',async()=>{show();await userEvent.click(screen.getByRole('button',{name:'Reject'}));expect(screen.getByRole('status')).toHaveTextContent('Approval rejected.');expect(store.action).toHaveBeenCalledExactlyOnceWith('/api/approvals/a1/reject',{decisionNote:null})})
