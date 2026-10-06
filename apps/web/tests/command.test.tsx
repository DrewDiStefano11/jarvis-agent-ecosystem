import { webcrypto } from 'node:crypto'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { beforeAll, beforeEach, expect, test, vi } from 'vitest'
import { JarvisCommand } from '../src/components/JarvisCommand'
import { useAppStore } from '../src/state/AppStore'
import type { Agent, Approval, Task } from '../src/types/contracts'
vi.mock('../src/state/AppStore',()=>({useAppStore:vi.fn()}))
let store: ReturnType<typeof useAppStore>
const task = {id:'completed-task',title:'Recorded research',request:'Research input',result:'Inspect the completed result',status:'completed',statusMessage:'Recorded outcome',parentTaskId:null} as Task
beforeAll(()=>{
  // jsdom has no top layer; actual API browser tests verify focus trapping/inertness.
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','')}
  HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new Event('close'))}
})
beforeEach(()=>{
  window.history.pushState({},'','/tasks');localStorage.clear();vi.stubGlobal('crypto',webcrypto)
  store={tasks:[task],agents:[{id:'demo-agent',name:'Demo Scout',role:'Researcher',status:'idle',capabilities:['fixture']} as Agent],approvals:[{id:'approval-1',taskId:task.id,title:'Review proposal',status:'pending',description:'Check bounded proposal'} as Approval],system:{},connection:'connected',error:null,resyncRequired:false,selectedAgentId:null,selectedTaskId:null,refresh:vi.fn().mockResolvedValue(undefined),selectTask:vi.fn(),selectAgent:vi.fn(),runtime:{identities:[{id:'real-agent',display_name:'Local planner',stable_key:'local-planner',description:'Registered planner',lifecycle_state:'active',is_enabled:true}],identityError:null,identityLoading:false,loadIdentities:vi.fn().mockResolvedValue(undefined),selectActor:vi.fn(),setTaskId:vi.fn()}} as unknown as ReturnType<typeof useAppStore>
  vi.mocked(useAppStore).mockImplementation(()=>store)
})
const renderCommand=()=>render(<BrowserRouter><JarvisCommand/></BrowserRouter>)
const open=async()=>{await userEvent.click(screen.getByRole('button',{name:'Talk to Jarvis'}));return screen.getByRole('dialog',{name:'Talk to Jarvis'})}
test('shortcut opens real grouped lookup and task result search uses shared inspection',async()=>{
  renderCommand();fireEvent.keyDown(window,{key:'k',ctrlKey:true})
  const dialog=await screen.findByRole('dialog',{name:'Talk to Jarvis'})
  expect(store.runtime.loadIdentities).toHaveBeenCalledOnce()
  expect(within(dialog).getByRole('region',{name:'Registered identities'})).toBeVisible()
  await userEvent.type(within(dialog).getByLabelText('Find records and pages'),'completed result')
  await userEvent.click(within(dialog).getByRole('button',{name:/Recorded research/}))
  expect(store.selectTask).toHaveBeenCalledWith(task.id)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
test('registered identity targets existing Planning without granting commands',async()=>{
  renderCommand();const dialog=await open()
  await userEvent.click(within(dialog).getByRole('button',{name:/Local planner/}))
  expect(store.runtime.selectActor).toHaveBeenCalledWith('real-agent')
  expect(window.location.pathname).toBe('/runtime')
})
test('simulated agents are distinguished and approvals navigate to the existing inbox',async()=>{
  renderCommand();let dialog=await open()
  await userEvent.click(within(dialog).getByRole('button',{name:/Demo Scout.*simulated agent/}))
  expect(store.selectAgent).toHaveBeenCalledWith('demo-agent')
  dialog=await open();await userEvent.click(within(dialog).getByRole('button',{name:/Review proposal/}))
  expect(window.location.pathname).toBe('/approvals')
})
test('draft survives close and mode switching; stale state blocks the reused creation form',async()=>{
  const app=renderCommand();let dialog=await open()
  await userEvent.click(within(dialog).getByRole('button',{name:'New request'}))
  await userEvent.type(within(dialog).getByLabelText('Title'),'Keep this draft')
  await userEvent.click(within(dialog).getByRole('button',{name:'Close Talk to Jarvis'}))
  dialog=await open();expect(within(dialog).getByLabelText('Title')).toHaveValue('Keep this draft')
  await userEvent.click(within(dialog).getByRole('button',{name:'Find records'}))
  await userEvent.click(within(dialog).getByRole('button',{name:'New request'}))
  expect(within(dialog).getByLabelText('Title')).toHaveValue('Keep this draft')
  store={...store,connection:'offline'};app.rerender(<BrowserRouter><JarvisCommand/></BrowserRouter>)
  expect(within(dialog).getByRole('button',{name:'Create task'})).toBeDisabled()
  expect(within(dialog).getByText(/New requests are unavailable/)).toBeVisible()
})
test('creation acknowledgement remains visible after closing while submission is pending',async()=>{
  let resolve!: (value:Response)=>void
  vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(finish=>{resolve=finish})))
  renderCommand();let dialog=await open();await userEvent.click(within(dialog).getByRole('button',{name:'New request'}))
  await userEvent.type(within(dialog).getByLabelText('Title'),'Queued request')
  await userEvent.type(within(dialog).getByLabelText('Description'),'Create and wait for acknowledgement')
  await userEvent.click(within(dialog).getByRole('button',{name:'Create task'}))
  await waitFor(()=>expect(fetch).toHaveBeenCalledOnce())
  await userEvent.click(within(dialog).getByRole('button',{name:'Close Talk to Jarvis'}))
  resolve({ok:true,json:async()=>({data:{id:'new-task',title:'Queued request'}})} as Response)
  await waitFor(()=>expect(store.refresh).toHaveBeenCalledOnce())
  dialog=await open();expect(await within(dialog).findByRole('heading',{name:'Task created and queued'})).toBeVisible()
  await userEvent.click(within(dialog).getByRole('button',{name:'Open planning for this task'}))
  expect(store.runtime.setTaskId).toHaveBeenCalledWith('new-task')
})
test('lookup bounds each group and searches all loaded records without mutating them',async()=>{
  store.tasks=Array.from({length:30},(_,index)=>({...task,id:`task-${index}`,title:`Record ${index}`}))
  const original=store.tasks.slice();renderCommand();const dialog=await open()
  const group=within(dialog).getByRole('region',{name:'Task records'})
  expect(within(group).getAllByRole('button')).toHaveLength(8)
  expect(within(group).getByText('Showing 8 of 30. Refine the search.')).toBeVisible()
  await userEvent.type(within(dialog).getByLabelText('Find records and pages'),'Record 29')
  expect(within(dialog).getByRole('button',{name:/Record 29/})).toBeVisible()
  expect(store.tasks).toEqual(original)
})
test('shortcut does not open over details and repeat events do not toggle it',async()=>{
  store.selectedTaskId='task';const app=renderCommand();fireEvent.keyDown(window,{key:'k',ctrlKey:true})
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  store={...store,selectedTaskId:null};app.rerender(<BrowserRouter><JarvisCommand/></BrowserRouter>)
  fireEvent.keyDown(window,{key:'k',ctrlKey:true,repeat:true});expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.keyDown(window,{key:'k',metaKey:true});expect(screen.getByRole('dialog')).toBeVisible()
})
test('Tab wraps visible controls in both directions and Escape closes a nonempty search',async()=>{
  renderCommand();const dialog=await open()
  const controls=within(dialog).getAllByRole('button')
  const first=controls[0]!,last=controls.at(-1)!
  last.focus();fireEvent.keyDown(last,{key:'Tab'});expect(first).toHaveFocus()
  fireEvent.keyDown(first,{key:'Tab',shiftKey:true});expect(last).toHaveFocus()
  const input=within(dialog).getByLabelText('Find records and pages')
  await userEvent.type(input,'Recorded')
  fireEvent.keyDown(input,{key:'Escape'})
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
