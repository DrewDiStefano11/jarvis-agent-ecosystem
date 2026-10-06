import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import { Audit } from '../src/pages/Audit'
import { useAppStore } from '../src/state/AppStore'
import type { AuditEvent } from '../src/types/contracts'
vi.mock('../src/state/AppStore',()=>({useAppStore:vi.fn()}))
const event:AuditEvent={id:'e1',timestamp:'2026-10-06T12:00:00Z',eventType:'task.created',actorAgentId:'scout',taskId:'t1',previousState:null,newState:'queued',summary:'Task created',correlationId:'c1',sequenceNumber:1,payload:{simulated:true},artifactIds:[],approvalId:null}
let store:ReturnType<typeof useAppStore>
beforeEach(()=>{
 store={auditEvents:[event],agents:[{id:'scout',name:'Scout'}],tasks:[{id:'t1',title:'Research task'}],connection:'connected',error:null,resyncRequired:false,loading:false,refresh:vi.fn(),selectTask:vi.fn()} as unknown as typeof store
 vi.mocked(useAppStore).mockImplementation(()=>store)
})
const show=()=>render(<Audit/>)
test('sequence order, row caps and filtering preserve shared collection',async()=>{
 store.auditEvents=Array.from({length:205},(_,i)=>({...event,id:`e${i}`,sequenceNumber:i,summary:`Event ${i}`}));const original=[...store.auditEvents];show()
 expect(screen.getAllByRole('listitem')).toHaveLength(50);expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('Event 204');await userEvent.click(screen.getByRole('button',{name:'Load 50 more'}));await userEvent.click(screen.getByRole('button',{name:'Load 50 more'}));await userEvent.click(screen.getByRole('button',{name:'Load 50 more'}));expect(screen.getAllByRole('listitem')).toHaveLength(200);expect(screen.getByText(/Display limited to 200/)).toBeInTheDocument();expect(store.auditEvents).toEqual(original)
 await userEvent.type(screen.getByRole('searchbox'),'Event 204');expect(screen.getAllByRole('listitem')).toHaveLength(1);expect(screen.getByText(/Showing 1 of 1 matching loaded/)).toBeInTheDocument()
})
test('unknown actor and task identifiers are retained and provenance is not inferred',async()=>{
 store.auditEvents=[{...event,actorAgentId:'actor-unknown',taskId:'task-unknown',payload:{}}];show();expect(screen.getByText('actor-unknown · task-unknown')).toBeInTheDocument();expect(screen.queryByText('Demonstration')).not.toBeInTheDocument();await userEvent.click(screen.getByText('Task created'));expect(await screen.findByText('Provenance not supplied by this record')).toBeInTheDocument();expect(screen.queryByRole('button',{name:'Open task'})).not.toBeInTheDocument()
})
test('system actor and identifier named system remain separately filterable',async()=>{
 store.auditEvents=[{...event,actorAgentId:null},{...event,id:'e2',actorAgentId:'system',eventType:'approval.approved'}];show();await userEvent.selectOptions(screen.getByLabelText('Actor'),'actor:system');expect(screen.getAllByRole('listitem')).toHaveLength(1);expect(screen.getByRole('listitem')).toHaveTextContent('approval.approved');await userEvent.selectOptions(screen.getByLabelText('Actor'),'system');expect(screen.getByRole('listitem')).toHaveTextContent('task.created')
})
test('category filters loaded audit records and absent results are honest',async()=>{
 store.auditEvents=[event,{...event,id:'e2',eventType:'approval.approved'}];show();await userEvent.selectOptions(screen.getByLabelText('Category'),'approval');expect(screen.getAllByRole('listitem')).toHaveLength(1);await userEvent.type(screen.getByRole('searchbox'),'absent');expect(screen.getByText('No activity matches these filters.')).toBeInTheDocument()
})
test('payload disclosure is deferred, copy uses only displayed redacted evidence and task action stays shared',async()=>{
 const user=userEvent.setup();store.auditEvents=[{...event,payload:{password:'private-value',html:'<script>unsafe()</script>',long:'x'.repeat(10000)}}];show();expect(screen.queryByRole('button',{name:'Copy displayed evidence'})).not.toBeInTheDocument();await user.click(screen.getByText('Task created'));const evidence=await screen.findByLabelText('Displayed event evidence');expect(evidence).not.toHaveTextContent('private-value');expect(evidence).toHaveTextContent('<script>');expect(screen.getByText(/Content truncated/)).toBeInTheDocument();const write=vi.spyOn(navigator.clipboard,'writeText').mockResolvedValue();await user.click(screen.getByRole('button',{name:'Copy displayed evidence'}));expect(write).toHaveBeenCalledExactlyOnceWith(evidence.textContent);expect(screen.getByRole('status')).toHaveTextContent('Displayed evidence copied');await user.click(screen.getByRole('button',{name:'Open task'}));expect(store.selectTask).toHaveBeenCalledExactlyOnceWith('t1')
})
test('clipboard failure has no false success',async()=>{
 const user=userEvent.setup();show();await user.click(screen.getByText('Task created'));await screen.findByRole('button',{name:'Copy displayed evidence'});vi.spyOn(navigator.clipboard,'writeText').mockRejectedValue(new Error('Denied'));await user.click(screen.getByRole('button',{name:'Copy displayed evidence'}));expect(screen.getByRole('status')).toHaveTextContent('Clipboard unavailable')
})
test('stale data retains useful records and refresh belongs to shared store',async()=>{
 store.error='HTTP unavailable';show();expect(screen.getByRole('status')).toHaveTextContent('Last-known activity');expect(screen.getByText('Task created')).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'Refresh state'}));expect(store.refresh).toHaveBeenCalledOnce()
})
test('unknown and empty collections are distinguished',()=>{
 store.auditEvents=[];store.connection='offline';const view=show();expect(screen.getByText('Activity records have not been confirmed.')).toBeInTheDocument();store.connection='connected';view.rerender(<Audit/>);expect(screen.getByText('No stored audit records are available.')).toBeInTheDocument()
})
test('native disclosure can close without keeping raw evidence mounted',async()=>{
 show();await userEvent.click(screen.getByText('Task created'));await screen.findByRole('button',{name:'Copy displayed evidence'});await userEvent.click(screen.getByText('Task created'));expect(screen.queryByRole('button',{name:'Copy displayed evidence'})).not.toBeInTheDocument()
})
test('selected filters stay visible when a shared refresh removes matching records',async()=>{
 const view=show();await userEvent.selectOptions(screen.getByLabelText('Actor'),'actor:scout');await userEvent.selectOptions(screen.getByLabelText('Category'),'task');store.auditEvents=[];view.rerender(<Audit/>);expect(screen.getByLabelText('Actor')).toHaveValue('actor:scout');expect(screen.getByLabelText('Category')).toHaveValue('task');expect(screen.getByRole('option',{name:'Scout'})).toBeInTheDocument()
})
