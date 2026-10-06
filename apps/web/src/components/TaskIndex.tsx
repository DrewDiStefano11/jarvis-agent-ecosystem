import { Fragment, useState } from 'react'
import type { Agent, Approval, Task, TaskStatus } from '../types/contracts'
import '../styles/task-index.css'

const statuses: TaskStatus[] = ['queued','planning','assigned','in_progress','waiting','waiting_for_approval','under_review','revision_requested','paused','failed','retrying','completed','cancelled']
const terminal = new Set(['completed','failed','cancelled'])
const previewLimit = 4000
export function TaskIndex({ tasks, agents, approvals, stale, loading, onInspect, onRefresh }: { tasks: Task[]; agents: Agent[]; approvals: Approval[]; stale: boolean; loading: boolean; onInspect: (id: string) => void; onRefresh: () => Promise<void> }) {
  const [search,setSearch] = useState('')
  const [view,setView] = useState('all')
  const [status,setStatus] = useState('all')
  const [assignment,setAssignment] = useState('all')
  const [priority,setPriority] = useState('all')
  const [limit,setLimit] = useState(50)
  const [refreshing,setRefreshing] = useState(false)
  const agentName = (id:string) => agents.find(a=>a.id===id)?.name ?? id
  const pendingCount = (task:Task) => approvals.filter(a=>task.approvalIds.includes(a.id) && a.status==='pending').length
  const needsAttention = (task:Task) => task.blockedBy.length>0 || task.status==='failed' || ['waiting_for_approval','under_review','revision_requested'].includes(task.status) || pendingCount(task)>0
  const assignments = Array.from(new Set([...tasks.flatMap(t=>t.assignedAgentIds),...(assignment==='all' || assignment==='unassigned'?[]:[assignment.slice(6)])])).sort((a,b)=>agentName(a).localeCompare(agentName(b)))
  const filtered = tasks.filter(task=>{
    const parent=tasks.find(t=>t.id===task.parentTaskId)
    const matchesView=view==='all' || (view==='active' && !terminal.has(task.status)) || (view==='attention' && needsAttention(task)) || (view==='blocked' && task.blockedBy.length>0) || (view==='approval' && (task.status==='waiting_for_approval' || pendingCount(task)>0)) || (view==='completed' && task.status==='completed') || (view==='failed' && ['failed','cancelled'].includes(task.status))
    return matchesView && (status==='all'||task.status===status) && (priority==='all'||task.priority===priority) && (assignment==='all'||(assignment==='unassigned'?task.assignedAgentIds.length===0:task.assignedAgentIds.includes(assignment.slice(6)))) && [task.title,task.id,task.request,task.result??'',task.statusMessage,task.error?.code??'',parent?.title??'',task.parentTaskId??'',...task.assignedAgentIds.map(agentName),...task.assignedAgentIds].join(' ').toLowerCase().includes(search.trim().toLowerCase())
  }).slice().sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt)||a.id.localeCompare(b.id))
  const shown=filtered.slice(0,limit)
  const filter=(set:(value:string)=>void,value:string)=>{set(value);setLimit(50)}
  return <section className="task-record-index" aria-label="Task records">
    {(loading||stale) && <p className="task-index-notice" role="status">{loading?'Loading task records.':'Last-known task data may be outdated. Refresh state.'}</p>}
    <div className="task-index-toolbar"><label>Search task records<input type="search" value={search} placeholder="Title, identifier, request or result" onChange={e=>filter(setSearch,e.target.value)}/></label>
      <label>View<select aria-label="View" value={view} onChange={e=>filter(setView,e.target.value)}>{[['all','All'],['active','Active'],['attention','Needs attention'],['blocked','Blocked'],['approval','Awaiting approval'],['completed','Completed'],['failed','Failed or cancelled']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      <label>Status<select aria-label="Status" value={status} onChange={e=>filter(setStatus,e.target.value)}><option value="all">All</option>{statuses.map(value=><option key={value} value={value}>{value.replaceAll('_',' ')}</option>)}</select></label>
      <label>Assigned agent<select aria-label="Assigned agent" value={assignment} onChange={e=>filter(setAssignment,e.target.value)}><option value="all">All</option><option value="unassigned">Unassigned</option>{assignments.map(id=><option key={id} value={`agent:${id}`}>{agentName(id)}</option>)}</select></label>
      <label>Priority<select aria-label="Priority" value={priority} onChange={e=>filter(setPriority,e.target.value)}><option value="all">All</option>{['urgent','high','medium','low'].map(value=><option key={value} value={value}>{value}</option>)}</select></label>
      <button disabled={refreshing} onClick={async()=>{setRefreshing(true);try{await onRefresh()}finally{setRefreshing(false)}}}>{refreshing?'Refreshing…':'Refresh state'}</button></div>
    <p className="task-index-scope">{shown.length} of {filtered.length} matching task records shown · {tasks.length} loaded. Updated most recently first.</p>
    {!shown.length&&!loading && <p>{tasks.length?'No task records match these filters.':stale?'Task records have not been confirmed.':'No task records are available.'}</p>}
    {shown.length>0 && <table className="task-index-table" role="table" aria-label="Task record index"><thead role="rowgroup"><tr role="row">{['Task','State','Assignment','Evidence','Updated','Inspect'].map(label=><th key={label} role="columnheader" scope="col">{label}</th>)}</tr></thead><tbody role="rowgroup">{shown.map(task=>{
      const parent=tasks.find(t=>t.id===task.parentTaskId)
      const pending=pendingCount(task)
      return <Fragment key={task.id}><tr role="row" className="task-index-row">
        <td role="cell" className="task-index-title"><strong>{task.title}</strong><small>{task.id}</small>{task.parentTaskId?<small>Subtask of <button className="task-index-link" disabled={!parent} onClick={()=>onInspect(task.parentTaskId!)}>{parent?.title??task.parentTaskId}</button></small>:<small>Root request</small>}{task.childTaskIds.length>0 && <details className="task-index-children"><summary>{task.childTaskIds.length} subtasks</summary>{task.childTaskIds.map(id=>{const child=tasks.find(t=>t.id===id);return <button key={id} disabled={!child} onClick={()=>onInspect(id)}>{child?.title??id}</button>})}</details>}{task.correctionOfTaskId && <small>Corrected follow-up to <button className="task-index-link" disabled={!tasks.some(t=>t.id===task.correctionOfTaskId)} onClick={()=>onInspect(task.correctionOfTaskId!)}>{tasks.find(t=>t.id===task.correctionOfTaskId)?.title??task.correctionOfTaskId}</button></small>}</td>
        <td role="cell" className="task-index-state"><span className={`task-record-status status-${task.status}`}>{task.status.replaceAll('_',' ')}</span><small>{task.priority} · {task.progress}% reported progress</small><small>{task.statusMessage.slice(0,180)}{task.statusMessage.length>180?"…":""}</small></td>
        <td role="cell" className="task-index-assignment">{task.assignedAgentIds.length?task.assignedAgentIds.map(agentName).join(', '):'Unassigned'}</td>
        <td role="cell" className="task-index-evidence">{task.error && <strong>{task.error.code}</strong>}{task.result!==null && <span>Result available</span>}<small>{task.blockedBy.length} blockers · {pending} pending approval records</small><small>{task.retryCount}/{task.maxRetries} retries</small></td>
        <td role="cell" className="task-index-updated"><time dateTime={task.updatedAt}>{new Date(task.updatedAt).toLocaleString()}</time>{task.completedAt && <small>Completed {new Date(task.completedAt).toLocaleString()}</small>}</td>
        <td role="cell" className="task-index-inspect"><button aria-label={`Open ${task.title}`} onClick={()=>onInspect(task.id)}>Open</button></td>
      </tr>{task.status==='completed'&&task.result!==null && <tr role="row" className="task-index-result-row"><td role="cell" colSpan={6}><details><summary>Result preview for {task.title}</summary><pre>{task.result.slice(0,previewLimit)}</pre>{task.result.length>previewLimit && <p>Result truncated to {previewLimit.toLocaleString()} characters. Open task to inspect its recorded result.</p>}<p>Recorded task result. Runtime verification is inspected in Planning.</p></details></td></tr>}</Fragment>
    })}</tbody></table>}
    {shown.length<filtered.length&&limit<200 && <button className="task-index-load" onClick={()=>setLimit(Math.min(200,limit+50))}>Show 50 more records</button>}
    {limit>=200&&filtered.length>200 && <p className="task-index-notice">Display limited to 200 records. Refine the filters to inspect other loaded tasks.</p>}
  </section>
}
