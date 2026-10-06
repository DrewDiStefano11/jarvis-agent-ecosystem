import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { TaskIndex } from '../src/components/TaskIndex'
import type { Agent, Approval, Task } from '../src/types/contracts'
const root={id:'root',title:'Root request',request:'Original objective',description:'request',parentTaskId:null,childTaskIds:['child'],assignedAgentIds:['scout'],assignedManagerId:null,priority:'high',status:'in_progress',progress:40,statusMessage:'Working',blockedBy:[],approvalIds:['pending','approved'],retryCount:0,maxRetries:2,result:null,error:null,createdAt:'2026-10-06T12:00:00Z',updatedAt:'2026-10-06T12:00:00Z',completedAt:null} as unknown as Task
const child={...root,id:'child',title:'Child research',parentTaskId:'root',childTaskIds:[],status:'failed',assignedAgentIds:['unknown-identity'],error:{code:'RESEARCH_FAILED',message:'Unavailable'},approvalIds:[],retryCount:1} as Task
const done={...root,id:'done',title:'Completed report',status:'completed',childTaskIds:[],assignedAgentIds:[],approvalIds:['approved'],result:'Recorded report text',completedAt:'2026-10-06T13:00:00Z',updatedAt:'2026-10-06T13:00:00Z',progress:100} as Task
const agents=[{id:'scout',name:'Scout'}] as Agent[]
const approvals=[{id:'pending',status:'pending'},{id:'approved',status:'approved'}] as Approval[]
const inspect=vi.fn()
const props=(tasks:Task[]=[root,child,done])=>({tasks,agents,approvals,stale:false,loading:false,onInspect:inspect,onRefresh:vi.fn().mockResolvedValue(undefined)})
test('matching child stays visible with parent context and unknown assignment ID',async()=>{
 render(<TaskIndex {...props()}/>);await userEvent.type(screen.getByRole('searchbox'),'Child research');const table=screen.getByRole('table');expect(within(table).getByText('unknown-identity')).toBeInTheDocument();expect(within(table).getByRole('button',{name:'Root request'})).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'Open Child research'}));expect(inspect).toHaveBeenCalledWith('child')
})
test('completed lookup searches real results without claiming verified execution',async()=>{
 render(<TaskIndex {...props()}/>);await userEvent.selectOptions(screen.getByLabelText('View'),'completed');await userEvent.type(screen.getByRole('searchbox'),'Recorded report');expect(screen.getByText(/1 of 1 matching/)).toBeInTheDocument();await userEvent.click(screen.getByText('Result preview for Completed report'));expect(screen.getByText('Recorded report text')).toBeVisible();expect(screen.getByText(/Runtime verification is inspected in Planning/)).toBeInTheDocument()
})
test('attention counts pending records and excludes old approved IDs',async()=>{
 render(<TaskIndex {...props()}/>);await userEvent.selectOptions(screen.getByLabelText('View'),'attention');expect(screen.getByText(/2 of 2 matching/)).toBeInTheDocument();expect(screen.getByText('0 blockers · 1 pending approval records')).toBeInTheDocument();expect(screen.queryByText('Completed report')).not.toBeInTheDocument()
})
test('stable status and selected assignment survive refreshed records',async()=>{
 const p=props();const v=render(<TaskIndex {...p}/>);await userEvent.selectOptions(screen.getByLabelText('Status'),'failed');await userEvent.selectOptions(screen.getByLabelText('Assigned agent'),'agent:unknown-identity');v.rerender(<TaskIndex {...p} tasks={[root,done]}/>);expect(screen.getByLabelText('Status')).toHaveValue('failed');expect(screen.getByLabelText('Assigned agent')).toHaveValue('agent:unknown-identity');expect(screen.getByText('No task records match these filters.')).toBeInTheDocument()
})
test('result preview is inert bounded text and correction context uses actual source',async()=>{
 render(<TaskIndex {...props([{...done,correctionOfTaskId:'root',result:'<script>test()</script>'+ 'x'.repeat(5000)},root])}/>);await userEvent.click(screen.getByText('Result preview for Completed report'));expect(screen.getByText(/Result truncated to 4,000/)).toBeVisible();expect(document.querySelector('pre')?.textContent?.length).toBe(4000);expect(document.querySelector('pre')).toHaveTextContent('<script>');expect(screen.getByText(/Corrected follow-up to/)).toBeInTheDocument()
})
test('all child links and retry counts remain record evidence',async()=>{
 render(<TaskIndex {...props()}/>);await userEvent.click(screen.getByText('1 subtasks'));await userEvent.click(screen.getByRole('button',{name:'Child research'}));expect(inspect).toHaveBeenCalledWith('child');expect(screen.getByText('1/2 retries')).toBeInTheDocument();expect(screen.queryByText(/attempts/)).not.toBeInTheDocument()
})
test('rows are bounded, sorting does not mutate shared data and filters reset the bound',async()=>{
 const user=userEvent.setup();const tasks=Array.from({length:205},(_,i)=>({...done,id:`record-${i}`,title:`Record ${i}`,result:null}));const original=[...tasks];render(<TaskIndex {...props(tasks)}/>);const table=screen.getByRole('table',{name:'Task record index'});const rendered=()=>table.querySelectorAll('button[aria-label^="Open Record"]').length;expect(rendered()).toBe(50);for(let i=0;i<3;i++)await user.click(screen.getByRole('button',{name:'Show 50 more records'}));expect(rendered()).toBe(200);expect(screen.getByText(/Display limited to 200/)).toBeInTheDocument();expect(tasks).toEqual(original);const search=screen.getByRole('searchbox');await user.click(search);await user.paste('Record 204');expect(rendered()).toBe(1);await user.clear(search);expect(rendered()).toBe(50)
})
test('stale and unknown collection stays distinct from confirmed empty with shared refresh',async()=>{
 const p=props([]);const v=render(<TaskIndex {...p} stale/>);expect(screen.getByText('Task records have not been confirmed.')).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'Refresh state'}));expect(p.onRefresh).toHaveBeenCalledOnce();v.rerender(<TaskIndex {...p}/>);expect(screen.getByText('No task records are available.')).toBeInTheDocument()
})

test('blocked and awaiting approval views use reported record evidence',async()=>{
 render(<TaskIndex {...props([{...root,blockedBy:['dependency-id']},child,done])}/>);await userEvent.selectOptions(screen.getByLabelText('View'),'blocked');expect(screen.getByText(/1 of 1 matching/)).toBeInTheDocument();await userEvent.selectOptions(screen.getByLabelText('View'),'approval');expect(screen.getByText(/1 of 1 matching/)).toBeInTheDocument();expect(screen.getByRole('button',{name:'Open Root request'})).toBeInTheDocument()
})
