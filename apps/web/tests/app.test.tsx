import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from '../src/App'
import { AppStoreProvider } from '../src/state/AppStore'
import type { Agent, Approval, Artifact, AuditEvent, Department, Notification, SystemStatus, Task } from '../src/types/contracts'

const now='2026-01-15T14:00:00Z'
const agents:Agent[]=['jarvis','atlas','scout','archive','sentinel'].map((id,index)=>({id,schemaVersion:'1.0',name:id[0]!.toUpperCase()+id.slice(1),role:index===0?'Executive Manager':'Specialist',description:'Simulated agent',goals:['Be transparent'],departmentId:index===0?'executive':'research',managerId:index===0?null:'jarvis',status:'idle',currentTaskId:null,queuedTaskIds:[],progress:0,statusMessage:'Available',capabilities:['simulation'],allowedTools:['fixture'],deniedTools:['shell'],approvalPolicy:{},memoryAccess:{},performance:{completionRate:.9,accuracyScore:.9,averageCompletionTime:10,failedTaskCount:0,userCorrectionCount:0,reviewerScore:.9,reliabilityScore:.95},resourceProfile:{},office:{zone:index===0?'Executive':'Research',deskId:`D-${index}`,spriteIdentifier:id,displayPosition:{x:1,y:1},currentAnimationState:'idle',currentDestination:null,isInMeeting:false},createdAt:now,updatedAt:now,version:'1',deploymentStatus:'simulated',isTemporary:false}))
const tasks:Task[]=[{id:'task-parent',schemaVersion:'1.0',title:'Caribbean recommendation',description:'Fixture trip',request:'Plan a trip',parentTaskId:null,childTaskIds:['task-child'],projectId:null,createdBy:'user',assignedManagerId:'jarvis',assignedAgentIds:['scout'],priority:'high',status:'in_progress',progress:45,statusMessage:'Researching fixtures',dependencies:[],blockedBy:[],approvalIds:['approval-pending'],artifactIds:[],result:null,error:null,retryCount:0,maxRetries:2,createdAt:now,startedAt:now,updatedAt:now,completedAt:null},{id:'task-child',schemaVersion:'1.0',title:'Research destinations',description:'Child',request:'Research',parentTaskId:'task-parent',childTaskIds:[],projectId:null,createdBy:'jarvis',assignedManagerId:'atlas',assignedAgentIds:['scout'],priority:'medium',status:'in_progress',progress:30,statusMessage:'Working',dependencies:[],blockedBy:[],approvalIds:[],artifactIds:[],result:null,error:null,retryCount:0,maxRetries:2,createdAt:now,startedAt:now,updatedAt:now,completedAt:null}]
const approvals:Approval[]=[{id:'approval-pending',taskId:'task-parent',requestedByAgentId:'scout',actionType:'simulated_publish',title:'Publish report',description:'Simulation only',reason:'Review gate',riskLevel:'yellow',affectedResources:['fixture'],exactActionPreview:'Save fixture',expectedOutcome:'Artifact appears',reversalMethod:'Reset',expiresAt:'2035-01-01T00:00:00Z',status:'pending',reviewedBy:null,reviewedAt:null,decisionNote:null,createdAt:now},{id:'approval-expired',taskId:'task-parent',requestedByAgentId:'scout',actionType:'simulated_read',title:'Expired request',description:'Expired simulation',reason:'Test',riskLevel:'orange',affectedResources:['fixture'],exactActionPreview:'None',expectedOutcome:'None',reversalMethod:'None',expiresAt:'2020-01-01T00:00:00Z',status:'expired',reviewedBy:null,reviewedAt:null,decisionNote:null,createdAt:now},{id:'approval-black',taskId:'task-parent',requestedByAgentId:'sentinel',actionType:'prohibited',title:'Prohibited action',description:'Never allowed',reason:'Boundary',riskLevel:'black',affectedResources:['external'],exactActionPreview:'None',expectedOutcome:'Refused',reversalMethod:'None',expiresAt:'2035-01-01T00:00:00Z',status:'pending',reviewedBy:null,reviewedAt:null,decisionNote:null,createdAt:now}]
const departments:Department[]=[{id:'executive',name:'Executive',description:'',managerAgentId:'jarvis',agentIds:['jarvis']},{id:'research',name:'Research and Knowledge',description:'',managerAgentId:'atlas',agentIds:['atlas','scout','archive','sentinel']}]
const auditEvents:AuditEvent[]=[{id:'audit-1',timestamp:now,eventType:'task.created',actorAgentId:'jarvis',taskId:'task-parent',previousState:'queued',newState:'in_progress',summary:'Task entered research',correlationId:'demo',sequenceNumber:1,payload:{simulated:true},artifactIds:[],approvalId:null}]
const notifications:Notification[]=[{id:'n1',title:'Hello',message:'Simulation',level:'info',isRead:false,taskId:null,createdAt:now}]
const artifacts:Artifact[]=[]
const system:SystemStatus={status:'healthy',environment:'test',apiSchemaVersion:'1.0',seedDataVersion:'2.0',emergencyStop:false,simulator:{state:'running',currentStep:4,totalSteps:25,accelerated:true},resources:[{name:'CPU',value:'fixture 18%',label:'Simulated'}],lastSynchronizedAt:now,storageBackend:'sqlite',databaseHealthy:true,databaseRevision:'20260724_03',schemaCurrent:true,eventSessionId:'session-test',outboxPendingCount:0,outboxExhaustedCount:0,recoveryRequired:false,activeWorkflowRunId:'run-test',lastCheckpointId:'checkpoint-test',lastStartupAt:now,lastCleanShutdown:null,activeWorkerCount:0,activeLeaseCount:0,expiredLeaseCount:0,staleWorkerCount:0,contextAssembler:{state:'ready',totalAssemblies:2,completedAssemblies:1,reviewRequiredAssemblies:1,includedSources:3,excludedSources:1,redactions:2,injectionFindings:1,lastAssemblyAt:now}}
const endpointData:Record<string,unknown>={'/api/departments':departments,'/api/agents':agents,'/api/tasks':tasks,'/api/approvals':approvals,'/api/artifacts':artifacts,'/api/audit-events':auditEvents,'/api/notifications':notifications,'/api/system/status':system}

class FakeWebSocket {static instances:FakeWebSocket[]=[];static CONNECTING=0;static OPEN=1;readyState=1;sent:string[]=[];onopen:(()=>void)|null=null;onmessage:((event:{data:string})=>void)|null=null;onerror:(()=>void)|null=null;onclose:(()=>void)|null=null;constructor(public url:string){FakeWebSocket.instances.push(this);queueMicrotask(()=>this.onopen?.())}close(){}send(value:string){this.sent.push(value)}emit(event:unknown){this.onmessage?.({data:JSON.stringify(event)})}emitRaw(value:string){this.onmessage?.({data:value})}}

function renderApp(){return render(<BrowserRouter><AppStoreProvider><App/></AppStoreProvider></BrowserRouter>)}
beforeEach(() => localStorage.clear())
beforeEach(()=>{window.history.pushState({},'', '/');FakeWebSocket.instances=[];vi.stubGlobal('WebSocket',FakeWebSocket);vi.stubGlobal('fetch',vi.fn(async(input:string|URL|Request,init?:RequestInit)=>{const path=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url).pathname;const data=endpointData[path]??(init?.method==='POST'?{}:[]);return {ok:true,status:200,json:async()=>({data,meta:{schemaVersion:'1.0'}})} as Response}))})

describe('Jarvis interface',()=>{
 test('dashboard renders synchronized seed state',async()=>{renderApp();expect(await screen.findByText('Good evening, operator.')).toBeInTheDocument();expect(screen.getByText('Available agents')).toBeInTheDocument();expect(screen.getByText('fixture 18%')).toBeInTheDocument()})
 test('routes set a useful title and unknown paths render a recoverable 404',async()=>{window.history.pushState({},'','/missing-view');renderApp();expect(await screen.findByRole('heading',{name:'Page not found'})).toBeInTheDocument();expect(document.title).toBe('Not found · Jarvis');await userEvent.click(screen.getByRole('link',{name:'Return to dashboard'}));expect(await screen.findByText('Good evening, operator.')).toBeInTheDocument();expect(document.title).toBe('Dashboard · Jarvis')})
 test('agent list and shared details render',async()=>{renderApp();await screen.findByText('Good evening, operator.');await userEvent.click(screen.getAllByRole('link',{name:'Agents'})[0]!);expect(await screen.findByText('Five permanent operators')).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'Open Jarvis'}));expect(screen.getByRole('dialog',{name:'Jarvis'})).toBeInTheDocument();expect(screen.getByText('Tool policy')).toBeInTheDocument()})
 test('task hierarchy renders and opens details',async()=>{renderApp();await screen.findByText('Good evening, operator.');await userEvent.click(screen.getAllByRole('link',{name:'Tasks'})[0]!);expect(await screen.findByText('1 subtasks')).toBeInTheDocument();await userEvent.click(screen.getByRole('button',{name:'Open Caribbean recommendation'}));expect(screen.getByText('Original request')).toBeInTheDocument();expect(screen.getByText('Research destinations')).toBeInTheDocument()})
 test('pending approval renders and policy disables unsafe actions',async()=>{renderApp();await screen.findByText('Good evening, operator.');await userEvent.click(screen.getAllByRole('link',{name:'Approvals'})[0]!);expect(await screen.findByText('Publish report')).toBeInTheDocument();const cards=screen.getAllByRole('article');expect(cards[1]!.querySelector('button')).toBeDisabled();expect(cards[2]!.querySelector('button')).toBeDisabled();expect(screen.getByText(/Black-risk actions are prohibited/)).toBeInTheDocument()})
 test('approval action refreshes UI',async()=>{renderApp();await screen.findByText('Good evening, operator.');await userEvent.click(screen.getAllByRole('link',{name:'Approvals'})[0]!);await userEvent.click(screen.getAllByRole('button',{name:'Approve'})[0]!);await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('Approval approved'))})
 test('emergency-stop state is visible in system data',async()=>{endpointData['/api/system/status']={...system,emergencyStop:true,simulator:{...system.simulator,state:'paused'}};renderApp();expect(await screen.findByRole('button',{name:'Resume system'})).toBeInTheDocument();endpointData['/api/system/status']=system})
 test('durable status and recovery controls are visible',async()=>{endpointData['/api/system/status']={...system,recoveryRequired:true,simulator:{...system.simulator,state:'recovery_required'}};window.history.pushState({},'','/system');renderApp();expect(await screen.findByText(/20260724_03/)).toBeInTheDocument();expect(screen.getByRole('alert')).toHaveTextContent('last checkpoint');expect(screen.getByRole('button',{name:'Resume demo'})).toBeInTheDocument();endpointData['/api/system/status']=system})
 test('context assembler metrics are visible',async()=>{window.history.pushState({},'','/system');renderApp();expect(await screen.findByText('Context assembler')).toBeInTheDocument();expect(screen.getByText('Context assemblies').nextElementSibling).toHaveTextContent('2');expect(screen.getByText('Context redactions').nextElementSibling).toHaveTextContent('2')})
 test('exhausted outbox state is visible',async()=>{endpointData['/api/system/status']={...system,status:'degraded',outboxPendingCount:1,outboxExhaustedCount:1};window.history.pushState({},'','/system');renderApp();expect(await screen.findByRole('alert')).toHaveTextContent('Outbox delivery is exhausted for 1 durable event');expect(screen.getByText('Exhausted outbox').nextElementSibling).toHaveTextContent('1');endpointData['/api/system/status']=system})
 test('task lease health and worker metrics are visible',async()=>{endpointData['/api/system/status']={...system,status:'degraded',activeWorkerCount:3,activeLeaseCount:2,expiredLeaseCount:1,staleWorkerCount:1};window.history.pushState({},'','/system');renderApp();const alerts=await screen.findAllByRole('alert');expect(alerts.map(item=>item.textContent).join(' ')).toContain('1 expired task lease is awaiting recovery');expect(alerts.map(item=>item.textContent).join(' ')).toContain('1 active worker heartbeat is stale');expect(screen.getByText('Active workers').nextElementSibling).toHaveTextContent('3');expect(screen.getByText('Active task leases').nextElementSibling).toHaveTextContent('2');endpointData['/api/system/status']=system})
 test('mobile navigation exposes every destination including Office and System',async()=>{renderApp();const navigation=await screen.findByRole('navigation',{name:'Mobile primary'});expect(navigation).toBeInTheDocument();expect(navigation.querySelector('a[href="/office"]')).toBeInTheDocument();expect(navigation.querySelector('a[href="/system"]')).toBeInTheDocument()})
 test('offline browser state is announced',async()=>{renderApp();await screen.findByText('Good evening, operator.');act(()=>window.dispatchEvent(new Event('offline')));expect(screen.getAllByText('offline').length).toBeGreaterThan(0)})
 test('office uses shared agents and opens shared detail',async()=>{renderApp();window.history.pushState({},'','/office');await act(async()=>window.dispatchEvent(new PopStateEvent('popstate')));await userEvent.click(await screen.findByRole('button',{name:/Jarvis/}));expect(screen.getByRole('dialog',{name:'Jarvis'})).toBeInTheDocument()})
 test('candidate inspection enables visible review rendering',async()=>{window.history.pushState({},'','/office');renderApp();await userEvent.click(await screen.findByLabelText('Inspect candidate geometry'));expect(document.querySelector('.office-viewport-shell')).toHaveClass('office-viewport-shell--candidate')})
 test('agent drawer traps focus, closes with Escape, and restores its trigger',async()=>{renderApp();await screen.findByText('Good evening, operator.');await userEvent.click(screen.getAllByRole('link',{name:'Agents'})[0]!);const trigger=screen.getByRole('button',{name:'Open Jarvis'});await userEvent.click(trigger);const close=screen.getByRole('button',{name:'Close agent details'});expect(close).toHaveFocus();await userEvent.keyboard('{Shift>}{Tab}{/Shift}');expect(document.activeElement).toBe(close);await userEvent.keyboard('{Escape}');expect(screen.queryByRole('dialog',{name:'Jarvis'})).not.toBeInTheDocument();expect(trigger).toHaveFocus()})
 test('duplicate websocket sequence is ignored',async()=>{renderApp();await screen.findByText('Good evening, operator.');const before=vi.mocked(fetch).mock.calls.length;const event={eventId:'e1',schemaVersion:'1',eventType:'noop',timestamp:now,sequenceNumber:2,correlationId:'c',taskId:null,agentId:null,source:'test',payload:{}};act(()=>{FakeWebSocket.instances[0]!.emit(event);FakeWebSocket.instances[0]!.emit(event)});await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(before+8))})
 test('out-of-order websocket event triggers HTTP resynchronization',async()=>{renderApp();await screen.findByText('Good evening, operator.');const before=vi.mocked(fetch).mock.calls.length;act(()=>FakeWebSocket.instances[0]!.emit({eventId:'e9',schemaVersion:'1',eventType:'noop',timestamp:now,sequenceNumber:9,correlationId:'c',taskId:null,agentId:null,source:'test',payload:{}}));await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(before))})
 test('malformed websocket data requests a safe resynchronization',async()=>{renderApp();await screen.findByText('Good evening, operator.');const socket=FakeWebSocket.instances[0]!;const before=vi.mocked(fetch).mock.calls.length;act(()=>socket.emitRaw('{not-json'));await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(before));expect(socket.sent).toContain('resync')})
 test('online notification reuses an already-open shared socket',async()=>{renderApp();await screen.findByText('Good evening, operator.');expect(FakeWebSocket.instances).toHaveLength(1);act(()=>window.dispatchEvent(new Event('online')));await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(8));expect(FakeWebSocket.instances).toHaveLength(1)})
 test('new event session accepts a zero snapshot after a high old sequence',async()=>{window.history.pushState({},'','/system');renderApp();await screen.findByText('test');const socket=FakeWebSocket.instances[0]!;const beforeOld=vi.mocked(fetch).mock.calls.length;act(()=>socket.emit({eventId:'old-20',schemaVersion:'1',eventType:'noop',timestamp:now,sequenceNumber:20,eventSessionId:'old-session',correlationId:'c',taskId:null,agentId:null,source:'test',payload:{}}));await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(beforeOld));act(()=>socket.emit({eventId:'new-0',schemaVersion:'1',eventType:'system.snapshot',timestamp:now,sequenceNumber:0,eventSessionId:'new-session',correlationId:'c',taskId:null,agentId:null,source:'test',payload:{snapshot:{departments,agents,tasks,approvals,artifacts,auditEvents,notifications,emergencyStop:false},system:{...system,environment:'new-session',eventSessionId:'new-session'}}}));expect(await screen.findByText('new-session')).toBeInTheDocument();const beforeNext=vi.mocked(fetch).mock.calls.length;act(()=>socket.emit({eventId:'new-1',schemaVersion:'1',eventType:'noop',timestamp:now,sequenceNumber:1,eventSessionId:'new-session',correlationId:'c',taskId:null,agentId:null,source:'test',payload:{}}));await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThan(beforeNext))})
})

describe('WebSocket session cursors',()=>{
 const event=(id:string,sequenceNumber:number,eventSessionId?:string|null,source='test')=>({eventId:id,schemaVersion:'1',eventType:'noop',timestamp:now,sequenceNumber,eventSessionId,correlationId:'c',taskId:null,agentId:null,source,payload:{}})
 test('runtime sessions do not reset simulator cursor',async()=>{renderApp();await screen.findByText('Good evening, operator.');const socket=FakeWebSocket.instances[0]!;const before=vi.mocked(fetch).mock.calls.length;act(()=>{socket.emit(event('sim-10',10,'simulator'));socket.emit(event('run-a-1',1,'runtime-a','agent_runtime'));socket.emit(event('sim-11',11,'simulator'))});await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(before+24))})
 test('interleaved runtime sessions track duplicates independently',async()=>{renderApp();await screen.findByText('Good evening, operator.');const socket=FakeWebSocket.instances[0]!;const before=vi.mocked(fetch).mock.calls.length;act(()=>{socket.emit(event('a-1',1,'runtime-a','agent_runtime'));socket.emit(event('b-1',1,'runtime-b','agent_runtime'));socket.emit(event('a-dup',1,'runtime-a','agent_runtime'));socket.emit(event('b-2',2,'runtime-b','agent_runtime'))});await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(before+24))})
 test('session gaps resynchronize without pinning the stale cursor',async()=>{renderApp();await screen.findByText('Good evening, operator.');const socket=FakeWebSocket.instances[0]!;const before=vi.mocked(fetch).mock.calls.length;act(()=>{socket.emit(event('a-1',1,'runtime-a','agent_runtime'));socket.emit(event('b-1',1,'runtime-b','agent_runtime'));socket.emit(event('a-3',3,'runtime-a','agent_runtime'));socket.emit(event('b-2',2,'runtime-b','agent_runtime'));socket.emit(event('a-4',4,'runtime-a','agent_runtime'))});await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(before+40));expect(socket.sent).toContain('resync')})
 test('legacy session and reconnect cursor reset are bounded',async()=>{renderApp();await screen.findByText('Good evening, operator.');let socket=FakeWebSocket.instances[0]!;const before=vi.mocked(fetch).mock.calls.length;act(()=>{socket.emit(event('legacy-2',2,null));socket.emit(event('legacy-dup',2,null))});await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(before+8));act(()=>socket.onclose?.());await waitFor(()=>expect(FakeWebSocket.instances.length).toBeGreaterThan(1),{timeout:3000});socket=FakeWebSocket.instances[1]!;const afterReconnect=vi.mocked(fetch).mock.calls.length;act(()=>socket.emit(event('runtime-high',20,'runtime-reconnected','agent_runtime')));await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(afterReconnect+8))})
 test('many runtime sessions do not break simulator sequence tracking',async()=>{renderApp();await screen.findByText('Good evening, operator.');const socket=FakeWebSocket.instances[0]!;const before=vi.mocked(fetch).mock.calls.length;const snapshotPayload={snapshot:{departments,agents,tasks,approvals,artifacts,auditEvents,notifications,emergencyStop:false},system};act(()=>{socket.emit(event('sim-1',1,'simulator'));for(let index=0;index<101;index+=1)socket.emit({...event(`runtime-${index}`,1,`runtime-${index}`,'agent_runtime'),eventType:'system.snapshot',payload:snapshotPayload});socket.emit(event('sim-2',2,'simulator'))});await waitFor(()=>expect(vi.mocked(fetch).mock.calls.length).toBe(before+16))})
})

describe('Local planning integration', () => {
 test('readiness and authorized durable results appear in the planning workspace', async () => {
  const identity = { id: 'actor-real', display_name: 'Local planner', stable_key: 'local-planner', lifecycle_state: 'active', operational_status: 'idle', is_enabled: true, agent_type: 'worker' }
  endpointData['/api/identity/agents'] = [identity]
  endpointData['/api/agent-runtime/runs'] = { items: [], next_offset: null, total_count: 0 }
  endpointData['/api/model-executions'] = [{ executionId: 'execution-real', runtimeRunId: 'run-real', runtimeAttemptId: 'attempt-real', taskId: tasks[0]!.id, targetAgentId: identity.id, workerId: 'worker-real', stage: 'completed', provider: 'ollama', model: 'configured-local', requestCount: 1, failureCode: null, result: { summary: 'Persisted planning output', analysis: 'A real stored result is visible.', recommendations: [], risks: [], assumptions: [], missingInformation: [], requiresHumanReview: false } }]
  window.history.pushState({}, '', '/runtime')
  renderApp()
  await screen.findByRole('heading', { name: 'Planning workspace' })
  expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeDisabled()
  await waitFor(() => expect(screen.getByLabelText('Act as local identity')).toHaveTextContent('Local planner'))
  await userEvent.selectOptions(screen.getByLabelText('Act as local identity'), 'actor-real')
  await userEvent.selectOptions(screen.getByLabelText('Task and history'), tasks[0]!.id)
  expect(await screen.findByText('Persisted planning output')).toBeInTheDocument()
  expect(vi.mocked(fetch).mock.calls.some(([, init]) => (init?.headers as Record<string, string>)?.['X-Jarvis-Actor-Id'] === 'actor-real')).toBe(true)
  await userEvent.selectOptions(screen.getByLabelText('Act as local identity'), '')
  expect(screen.queryByText('Persisted planning output')).not.toBeInTheDocument()
  delete endpointData['/api/identity/agents']; delete endpointData['/api/agent-runtime/runs']; delete endpointData['/api/model-executions']
 })
 test('task creation failure remains visible and retains the form', async () => {
  window.history.pushState({}, '', '/tasks'); renderApp()
  await screen.findByRole('button', { name: '+ New task' })
  await userEvent.click(screen.getByRole('button', { name: '+ New task' }))
  await userEvent.type(screen.getByLabelText('Title'), 'Plan next milestone')
  await userEvent.type(screen.getByLabelText('Description'), 'Produce a bounded plan for the next milestone.')
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (input, init) => init?.method === 'POST' ? ({ ok: false, status: 503, json: async () => ({ error: { code: 'UNAVAILABLE', message: 'Backend temporarily unavailable' } }) } as Response) : original(input, init))
  await userEvent.click(screen.getByRole('button', { name: 'Create task' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Backend temporarily unavailable')
  expect(screen.getByLabelText('Title')).toHaveValue('Plan next milestone')
 })
})

describe('operator control failures', () => {
 test('operator correction preserves its source and recovers one created task after an uncertain acknowledgement', async () => {
  const source = { ...tasks[0]!, status: 'under_review', result: 'Original reviewed result' }
  const corrected = { ...tasks[0]!, id: 'task-corrected', title: 'Corrected request', description: 'Use the clarified acceptance requirements.', status: 'queued', correctionOfTaskId: source.id }
  endpointData['/api/tasks'] = [source]
  window.history.pushState({}, '', `/tasks?correct=${source.id}`)
  const original = vi.mocked(fetch).getMockImplementation()!
  const creations: RequestInit[] = []
  vi.mocked(fetch).mockImplementation(async (input, init) => {
   if (String(input).endsWith('/api/tasks') && init?.method === 'POST') {
    creations.push(init)
    endpointData['/api/tasks'] = [source, corrected]
    if (creations.length === 1) throw new TypeError('Acknowledgement interrupted')
    return { ok: true, status: 201, json: async () => ({ data: corrected }) } as Response
   }
   return original(input, init)
  })
  try {
   renderApp()
   await screen.findByRole('heading', { name: 'Correct task input' })
   await userEvent.clear(screen.getByLabelText('Title'))
   await userEvent.type(screen.getByLabelText('Title'), corrected.title)
   await userEvent.clear(screen.getByLabelText('Description'))
   await userEvent.type(screen.getByLabelText('Description'), corrected.description)
   await userEvent.click(screen.getByRole('button', { name: 'Create corrected task' }))
   expect(await screen.findByRole('alert')).toHaveTextContent('Acknowledgement interrupted')
   expect(screen.getByLabelText('Description')).toBeDisabled()
   await userEvent.click(screen.getByRole('button', { name: 'Retry creation' }))
   const planning = await screen.findByRole('link', { name: 'Open planning for this task' })
   expect(creations).toHaveLength(2)
   expect(creations[0]).toEqual(creations[1])
   expect(JSON.parse(creations[0]!.body as string)).toEqual({ title: corrected.title, description: corrected.description, priority: source.priority, correctionOfTaskId: source.id })
   expect(source.status).toBe('under_review')
   expect(source.result).toBe('Original reviewed result')
   await userEvent.click(planning)
   expect(await screen.findByLabelText('Task and history')).toHaveValue(corrected.id)
   expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2)
  } finally { endpointData['/api/tasks'] = tasks }
 })

 test('an active task cannot open the correction form', async () => {
  window.history.pushState({}, '', `/tasks?correct=${tasks[0]!.id}`)
  renderApp()
  expect(await screen.findByRole('alert')).toHaveTextContent('This task is still active')
  expect(screen.queryByRole('button', { name: 'Create corrected task' })).not.toBeInTheDocument()
 })

 test('failed emergency stop remains visible and releases its control', async () => {
  renderApp(); await screen.findByText('Good evening, operator.')
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (input, init) => init?.method === 'POST' ? ({ ok: false, status: 503, json: async () => ({ error: { code: 'UNAVAILABLE', message: 'Stop was not accepted' } }) } as Response) : original(input, init))
  await userEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Stop was not accepted')
  expect(screen.getByRole('button', { name: 'Emergency stop' })).toBeEnabled()
 })
 test('task cancellation reaches the authoritative endpoint and reports failure', async () => {
  window.history.pushState({}, '', '/tasks'); renderApp()
  await userEvent.click(await screen.findByRole('button', { name: 'Open Caribbean recommendation' }))
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (input, init) => init?.method === 'POST' ? ({ ok: false, status: 409, json: async () => ({ error: { code: 'CONFLICT', message: 'Task already completed' } }) } as Response) : original(input, init))
  await userEvent.click(screen.getByRole('button', { name: 'Cancel task' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Task already completed')
  expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).endsWith('/api/tasks/task-parent/cancel') && init?.method === 'POST')).toBe(true)
 })
})

test('explicit task setup selects the provisioned identity without queueing inference', async () => {
 endpointData['/api/tasks'] = tasks.map(task => ({ ...task, status: 'queued' }))
 endpointData['/api/identity/agents'] = [{ id: 'actor-prepared', display_name: 'Local planner', lifecycle_state: 'active', is_enabled: true }]
 endpointData['/api/local-planning/setup'] = { actorId: 'actor-prepared', workerActorConfigured: true, executionEnabledBySetup: false }
 endpointData['/api/agent-runtime/runs'] = { items: [], next_offset: null, total_count: 0 }
 try {
  window.history.pushState({}, '', '/runtime'); renderApp()
  await screen.findByRole('heading', { name: 'Planning workspace' })
  await userEvent.selectOptions(screen.getByLabelText('Task and history'), tasks[0]!.id)
  await userEvent.click(screen.getByRole('button', { name: 'Prepare local planner for this task' }))
  expect(await screen.findByText('Local planner prepared for this task. Queue the plan when ready.')).toBeInTheDocument()
  expect(screen.getByLabelText('Act as local identity')).toHaveValue('actor-prepared')
  expect(screen.getByLabelText('Target agent')).toHaveValue('actor-prepared')
  expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeDisabled()
  expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).endsWith('/api/local-planning/setup') && init?.body === JSON.stringify({ taskId: tasks[0]!.id }))).toBe(true)
  expect(vi.mocked(fetch).mock.calls.some(([url, init]) => String(url).endsWith('/api/agent-runtime/commands') && init?.method === 'POST')).toBe(false)
 } finally {
  endpointData['/api/tasks'] = tasks
  delete endpointData['/api/identity/agents']; delete endpointData['/api/local-planning/setup']; delete endpointData['/api/agent-runtime/runs']
 }
})

describe('planning worker identity', () => {
 const worker = { enabled: true, modelExecutionMode: 'local_only', workerActorId: 'worker-a', status: 'healthy', reasonCode: null, activeExecutionCount: 0, queuedEligibleRuntimeCount: 0, completedExecutionCount: 0, failedExecutionCount: 0, reviewRequiredCount: 0, providerReady: true, lastWorkerHeartbeat: now, lastSuccessfulExecutionAt: null }
 const originalCrypto = globalThis.crypto
 beforeEach(() => {
  endpointData['/api/tasks'] = tasks.map(task => ({ ...task, status: 'queued' }))
  endpointData['/api/system/status'] = { ...system, autonomousWorker: worker }
  endpointData['/api/identity/agents'] = ['worker-a', 'actor-b'].map(id => ({ id, display_name: id === 'worker-a' ? 'Configured worker' : 'Other operator', stable_key: id, lifecycle_state: 'active', is_enabled: true, operational_status: 'idle', agent_type: 'worker' }))
  endpointData['/api/agent-runtime/runs'] = { items: [], next_offset: null, total_count: 0 }
  endpointData['/api/model-executions'] = []
  vi.stubGlobal('crypto', { randomUUID: () => 'planning-test', subtle: { digest: async () => new Uint8Array([1, 2]).buffer } })
 })
 afterEach(() => {
  endpointData['/api/tasks'] = tasks
  endpointData['/api/system/status'] = system
  delete endpointData['/api/identity/agents']; delete endpointData['/api/agent-runtime/runs']; delete endpointData['/api/model-executions']
  vi.stubGlobal('crypto', originalCrypto)
 })
 const openPlanning = async () => {
  window.history.pushState({}, '', '/runtime'); renderApp()
  await waitFor(() => expect(screen.getByLabelText('Act as local identity')).toHaveTextContent('Other operator'))
  await userEvent.selectOptions(screen.getByLabelText('Act as local identity'), 'actor-b')
  await userEvent.selectOptions(screen.getByLabelText('Target agent'), 'actor-b')
  await userEvent.selectOptions(screen.getByLabelText('Task and history'), tasks[0]!.id)
 }
 const acceptPlanningCommands = (failQueue = false) => {
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (input, init) => {
   const path = new URL(String(input)).pathname
   if (init?.method === 'POST' && path === '/api/context/assemblies') return { ok: true, status: 200, json: async () => ({ data: { id: 'context-test', status: 'completed' } }) } as Response
   if (init?.method === 'POST' && path === '/api/agent-runtime/commands') {
    const body = JSON.parse(String(init.body))
    if (failQueue && body.command_type === 'queue') return { ok: false, status: 503, json: async () => ({ error: { code: 'UNAVAILABLE', message: 'Queue response unavailable' } }) } as Response
    return { ok: true, status: 200, json: async () => ({ data: { snapshot: { specification: { run_id: 'run-planning-test' }, version: 1, state: body.command_type === 'queue' ? 'queued' : 'created' } } }) } as Response
   }
   return original(input, init)
  })
 }
 test('blocks another submitter, then queues as the explicitly selected worker without changing the target or grants', async () => {
  acceptPlanningCommands()
  await openPlanning()
  expect(screen.getByText(/Select the configured worker identity to queue a plan/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Queue local plan' }))
  expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: 'Use configured worker identity' }))
  expect(screen.getByLabelText('Act as local identity')).toHaveValue('worker-a')
  expect(screen.getByLabelText('Target agent')).toHaveValue('actor-b')
  await userEvent.click(screen.getByRole('button', { name: 'Queue local plan' }))
  expect(await screen.findByText(/Queued run-planning-test/)).toBeInTheDocument()
  const commands = vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/api/agent-runtime/commands') && init?.method === 'POST')
  expect(commands).toHaveLength(2)
  for (const [, init] of commands) {
   expect(init?.headers).toMatchObject({ 'X-Jarvis-Actor-Id': 'worker-a' })
   expect(JSON.parse(String(init?.body))).toMatchObject({ actor_reference: 'worker-a' })
  }
  expect(JSON.parse(String(commands[0]![1]?.body)).specification.agent_id).toBe('actor-b')
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/api/local-planning/setup'))).toBe(false)
 })
 test('does not allow global readiness to enable submission when worker identity is unconfigured', async () => {
  endpointData['/api/system/status'] = { ...system, autonomousWorker: { ...worker, workerActorId: null } }
  await openPlanning()
  expect(screen.getByText(/Configure JARVIS_AUTONOMOUS_WORKER_ACTOR_ID/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Use configured worker identity' })).not.toBeInTheDocument()
 })
 test('blocks retrying an old submission when the configured worker changes', async () => {
  acceptPlanningCommands(true)
  await openPlanning()
  await userEvent.click(screen.getByRole('button', { name: 'Use configured worker identity' }))
  await userEvent.click(screen.getByRole('button', { name: 'Queue local plan' }))
  expect(await screen.findByText('Queue response unavailable')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Retry same submission' })).toBeEnabled()
  endpointData['/api/system/status'] = { ...system, autonomousWorker: { ...worker, workerActorId: 'actor-b' } }
  act(() => FakeWebSocket.instances[0]!.emit({ eventId: 'worker-change', schemaVersion: '1', eventType: 'noop', timestamp: now, sequenceNumber: 1, correlationId: 'worker-change', taskId: null, agentId: null, source: 'test', payload: {} }))
  expect(await screen.findByText(/The configured worker identity changed/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Retry same submission' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Use configured worker identity' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Clear submission form' })).toBeEnabled()
 })
 test.each(['completed', 'failed', 'cancelled', 'under_review'])('blocks a pending submission when authoritative task becomes %s', async terminalStatus => {
  acceptPlanningCommands(true)
  await openPlanning()
  await userEvent.click(screen.getByRole('button', { name: 'Use configured worker identity' }))
  await userEvent.click(screen.getByRole('button', { name: 'Queue local plan' }))
  await screen.findByText('Queue response unavailable')
  const originalCommands = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST').length
  endpointData['/api/tasks'] = tasks.map(task => ({ ...task, status: terminalStatus }))
  act(() => FakeWebSocket.instances[0]!.emit({ eventId: 'task-terminal', schemaVersion: '1', eventType: 'task.changed', timestamp: now, sequenceNumber: 1, correlationId: 'task-terminal', taskId: tasks[0]!.id, agentId: null, source: 'test', payload: {} }))
  await screen.findByText(new RegExp('This task is now ' + terminalStatus))
  expect(screen.getByRole('button', { name: 'Retry same submission' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Retry same submission' }))
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(originalCommands)
  expect(screen.getByRole('button', { name: 'Clear submission form' })).toBeEnabled()
 })
})
