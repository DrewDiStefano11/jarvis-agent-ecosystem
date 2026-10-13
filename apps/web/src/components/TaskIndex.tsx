import { Fragment, useMemo, useState } from 'react'
import type { Agent, Approval, Task, TaskStatus } from '../types/contracts'
import type { RuntimeIdentity } from '../types/runtime'
import { NavIcon } from './NavIcon'
import { Status } from './Status'
import '../styles/task-index.css'

const statuses: TaskStatus[] = ['queued', 'planning', 'assigned', 'in_progress', 'waiting', 'waiting_for_approval', 'under_review', 'revision_requested', 'paused', 'failed', 'retrying', 'completed', 'cancelled']
const terminal = new Set(['completed', 'failed', 'cancelled'])
const previewLimit = 4000
const views = [['all', 'All'], ['active', 'Active'], ['attention', 'Needs attention'], ['blocked', 'Blocked'], ['approval', 'Awaiting approval'], ['completed', 'Completed'], ['failed', 'Failed or cancelled']] as const

type Props = {
  tasks: Task[]; agents: Agent[]; approvals: Approval[]; identities?: RuntimeIdentity[]
  stale: boolean; loading: boolean; confirmed?: boolean
  onInspect: (id: string) => void; onRefresh: () => Promise<void>
  onPlan?: (id: string) => void; onCorrect?: (id: string) => void; onApprovals?: () => void
}
export function TaskIndex({ tasks, agents, approvals, identities = [], stale, loading, confirmed = true, onInspect, onRefresh, onPlan, onCorrect, onApprovals }: Props) {
  const [search, setSearch] = useState('')
  const [view, setView] = useState('all')
  const [status, setStatus] = useState('all')
  const [assignment, setAssignment] = useState('all')
  const [priority, setPriority] = useState('all')
  const [limit, setLimit] = useState(50)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const byTask = useMemo(() => new Map(tasks.map(task => [task.id, task])), [tasks])
  const byAgent = useMemo(() => new Map(agents.map(agent => [agent.id, agent])), [agents])
  const byIdentity = useMemo(() => new Map(identities.map(identity => [identity.id, identity])), [identities])
  const byApproval = useMemo(() => new Map(approvals.map(approval => [approval.id, approval])), [approvals])
  const agentName = (id: string) => byIdentity.get(id)?.display_name ?? byAgent.get(id)?.name ?? id
  const pendingCount = (task: Task) => task.approvalIds.filter(id => byApproval.get(id)?.status === 'pending').length
  const needsAttention = (task: Task) => task.blockedBy.length > 0 || task.status === 'failed' || ['waiting_for_approval', 'under_review', 'revision_requested'].includes(task.status) || pendingCount(task) > 0
  const assignments = Array.from(new Set([...tasks.flatMap(task => task.assignedAgentIds), ...(assignment === 'all' || assignment === 'unassigned' ? [] : [assignment.slice(6)])])).sort((a, b) => agentName(a).localeCompare(agentName(b)))
  const filtered = tasks.filter(task => {
    const parent = byTask.get(task.parentTaskId ?? '')
    const matchesView = view === 'all' || (view === 'active' && !terminal.has(task.status)) || (view === 'attention' && needsAttention(task)) || (view === 'blocked' && task.blockedBy.length > 0) || (view === 'approval' && (task.status === 'waiting_for_approval' || pendingCount(task) > 0)) || (view === 'completed' && task.status === 'completed') || (view === 'failed' && ['failed', 'cancelled'].includes(task.status))
    return matchesView && (status === 'all' || task.status === status) && (priority === 'all' || task.priority === priority) && (assignment === 'all' || (assignment === 'unassigned' ? task.assignedAgentIds.length === 0 : task.assignedAgentIds.includes(assignment.slice(6)))) && [task.title, task.id, task.request, task.result ?? '', task.statusMessage, task.error?.code ?? '', parent?.title ?? '', task.parentTaskId ?? '', ...task.assignedAgentIds.map(agentName), ...task.assignedAgentIds].join(' ').toLowerCase().includes(search.trim().toLowerCase())
  }).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt) || a.id.localeCompare(b.id))
  const shown = filtered.slice(0, limit)
  const roots = tasks.filter(task => !task.parentTaskId)
  const pending = approvals.filter(approval => approval.status === 'pending').length
  const filter = (set: (value: string) => void, value: string) => { set(value); setLimit(50) }
  const reset = () => { setSearch(''); setView('all'); setStatus('all'); setAssignment('all'); setPriority('all'); setLimit(50) }
  const activeFilterCount = [status, assignment, priority].filter(value => value !== 'all').length
  const summaries = [
    { label: 'Open requests', count: roots.filter(task => !terminal.has(task.status)).length, detail: 'Root task records', icon: 'tasks', target: 'active' },
    { label: 'Needs attention', count: tasks.filter(needsAttention).length, detail: 'Loaded records', icon: 'attention', target: 'attention' },
    { label: 'Completed', count: roots.filter(task => task.status === 'completed').length, detail: 'Saved root outcomes', icon: 'approvals', target: 'completed' },
    { label: 'Pending decisions', count: pending, detail: 'Approval records', icon: 'attention', target: 'approval' },
  ] as const
  return <section className="task-record-index" aria-label="Task records">
    <div className="task-summary-strip" aria-label="Task snapshot summary">{summaries.map(item => <button key={item.label} onClick={() => { reset(); filter(setView, item.target); if (item.target === 'approval') onApprovals?.() }}><span className={`task-summary-icon summary-${item.target}`}><NavIcon name={item.icon}/></span><span><small>{item.label}</small><strong>{confirmed ? item.count : 'Unavailable'}</strong><small>{item.detail}</small></span></button>)}</div>
    {(loading || stale) && <p className="task-index-notice" role="status">{loading ? 'Loading task records.' : 'Last-known task data may be outdated. Refresh state.'}</p>}
    <div className="task-index-toolbar">
      <div className="task-view-tabs" role="group" aria-label="Task view">{views.filter(([value]) => ['all', 'active', 'attention', 'completed'].includes(value)).map(([value, label]) => <button key={value} aria-pressed={view === value} onClick={() => filter(setView, value)}>{label}</button>)}</div>
      <label className="task-search">Search task records<input type="search" value={search} placeholder="Title, request or saved result" onChange={event => filter(setSearch, event.target.value)}/></label>
      <button className="task-filter-toggle" aria-expanded={filtersOpen} aria-controls="task-advanced-filters" onClick={() => setFiltersOpen(value => !value)}><NavIcon name="tasks"/>Filters{activeFilterCount > 0 && <span>{activeFilterCount}</span>}</button>
      <button className="task-refresh-button" disabled={refreshing} onClick={async () => { setRefreshing(true); setRefreshError(''); try { await onRefresh() } catch (error) { setRefreshError(error instanceof Error ? error.message : 'State refresh failed') } finally { setRefreshing(false) } }}><NavIcon name="activity"/>{refreshing ? 'Refreshing...' : 'Refresh state'}</button>
    </div>
    <div className="task-advanced-filters" id="task-advanced-filters" hidden={!filtersOpen}>
      <label>View<select aria-label="View" value={view} onChange={event => filter(setView, event.target.value)}>{views.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Status<select aria-label="Status" value={status} onChange={event => filter(setStatus, event.target.value)}><option value="all">All</option>{statuses.map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
      <label>Assigned agent<select aria-label="Assigned agent" value={assignment} onChange={event => filter(setAssignment, event.target.value)}><option value="all">All</option><option value="unassigned">Unassigned</option>{assignments.map(id => <option key={id} value={`agent:${id}`}>{agentName(id)}</option>)}</select></label>
      <label>Priority<select aria-label="Priority" value={priority} onChange={event => filter(setPriority, event.target.value)}><option value="all">All</option>{['urgent', 'high', 'medium', 'low'].map(value => <option key={value}>{value}</option>)}</select></label>
      <button onClick={reset}>Clear filters</button>
    </div>
    {refreshError && <p className="task-index-notice" role="alert">{refreshError}</p>}
    <div className="task-index-scope"><span>{shown.length} of {filtered.length} matching task records shown · {tasks.length} loaded. Updated most recently first.</span>{(search || view !== 'all' || activeFilterCount > 0) && <button className="task-index-link" onClick={reset}>Clear filters</button>}</div>
    {!shown.length && !loading && <div className="task-index-empty"><NavIcon name="tasks"/><h2>{tasks.length ? 'No task records match these filters.' : stale || !confirmed ? 'Task records have not been confirmed.' : 'No task records are available.'}</h2><p>{tasks.length ? 'Clear a filter or try another request, identifier, or saved result.' : 'Start with a clear request. Creating it saves a task; planning and execution are separate actions.'}</p>{tasks.length > 0 && <button onClick={reset}>Reset view</button>}</div>}
    {shown.length > 0 && <div className="task-table-frame"><table className="task-index-table" role="table" aria-label="Task record index"><thead role="rowgroup"><tr role="row">{['Request', 'Progress', 'Assignment', 'Evidence', 'Updated', 'Actions'].map(label => <th key={label} role="columnheader" scope="col">{label}</th>)}</tr></thead><tbody role="rowgroup">{shown.map(task => {
      const parent = byTask.get(task.parentTaskId ?? '')
      const taskPending = pendingCount(task)
      const progress = Number.isFinite(task.progress) ? Math.max(0, Math.min(100, task.progress)) : 0
      const correctable = ['under_review', 'failed', 'cancelled', 'completed'].includes(task.status)
      return <Fragment key={task.id}><tr role="row" className="task-index-row">
        <td role="cell" className="task-index-title"><div className="task-request-heading"><span className={`task-request-symbol request-${task.status}`}><NavIcon name={task.error ? 'attention' : task.status === 'completed' ? 'approvals' : 'tasks'}/></span><div><strong>{task.title}</strong><small>{task.parentTaskId ? <>Subtask of <button className="task-index-link" disabled={!parent} onClick={() => onInspect(task.parentTaskId!)}>{parent?.title ?? task.parentTaskId}</button></> : 'Root request'} · {task.priority} priority</small></div></div><details className="task-record-identity"><summary>Record details</summary><code>{task.id}</code><small>{task.retryCount}/{task.maxRetries} retries</small>{task.error && <small>{task.error.code}</small>}{task.correctionOfTaskId && <small>Corrected follow-up to <button className="task-index-link" disabled={!byTask.has(task.correctionOfTaskId)} onClick={() => onInspect(task.correctionOfTaskId!)}>{byTask.get(task.correctionOfTaskId)?.title ?? task.correctionOfTaskId}</button></small>}</details>{task.childTaskIds.length > 0 && <details className="task-index-children"><summary>{task.childTaskIds.length} subtasks</summary>{task.childTaskIds.map(id => <button key={id} disabled={!byTask.has(id)} onClick={() => onInspect(id)}>{byTask.get(id)?.title ?? id}</button>)}</details>}</td>
        <td role="cell" className="task-index-state"><div className="task-state-primary"><span className="task-list-ring" aria-label={`${progress}% reported progress`}><svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="16" fill="none" stroke="#30394e" strokeWidth="3"/><circle cx="20" cy="20" r="16" fill="none" stroke="currentColor" strokeWidth="3" pathLength="100" strokeDasharray={`${progress} 100`} transform="rotate(-90 20 20)"/></svg><span>{progress}%</span></span><Status value={task.status}/></div><small>{task.statusMessage.slice(0, 180)}{task.statusMessage.length > 180 ? '...' : ''}</small></td>
        <td role="cell" className="task-index-assignment">{task.assignedAgentIds.length ? task.assignedAgentIds.map(id => <div className="task-assignee" key={id}><NavIcon name="agents"/><span>{agentName(id)}<small>{byIdentity.has(id) ? 'Registered identity' : byAgent.has(id) ? 'Demonstration agent' : 'Identity not loaded'}</small></span></div>) : <span className="task-unassigned">Unassigned</span>}</td>
        <td role="cell" className="task-index-evidence">{task.error && <strong>Error recorded</strong>}{task.result !== null && <span className="task-result-available"><NavIcon name="approvals"/>Result available</span>}<small>{task.blockedBy.length} blockers · {taskPending} pending approval records</small></td>
        <td role="cell" className="task-index-updated"><time dateTime={task.updatedAt}>{new Date(task.updatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}<small>{new Date(task.updatedAt).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}</small></time>{task.completedAt && <small>Completed {new Date(task.completedAt).toLocaleDateString()}</small>}</td>
        <td role="cell" className="task-index-inspect"><button className="task-inspect-action" aria-label={`Open ${task.title}`} onClick={() => onInspect(task.id)}>Inspect<NavIcon name="next"/></button><div className="task-row-secondary-actions">{onPlan && <button className="task-secondary-action" aria-label={`${task.result !== null ? 'View runtime evidence for' : 'Plan'} ${task.title}`} onClick={() => onPlan(task.id)}>{task.result !== null ? 'Runtime evidence' : 'Plan'}</button>}{onCorrect && correctable && <button className="task-secondary-action" aria-label={`Revise ${task.title}`} onClick={() => onCorrect(task.id)}>Revise input</button>}</div></td>
      </tr>{task.status === 'completed' && task.result !== null && <tr role="row" className="task-index-result-row"><td role="cell" colSpan={6}><details><summary><NavIcon name="approvals"/>Result preview for {task.title}</summary><pre>{task.result.slice(0, previewLimit)}</pre>{task.result.length > previewLimit && <p>Result truncated to {previewLimit.toLocaleString()} characters. Open task to inspect its recorded result.</p>}<p>Recorded task result. Runtime verification is inspected in Planning.</p><button onClick={() => onInspect(task.id)}>View full result</button></details></td></tr>}</Fragment>
    })}</tbody></table></div>}
    <p className="task-index-provenance">Task records may include demonstration work. Runtime evidence is inspected separately. Progress is reported by the task record.</p>
    {shown.length < filtered.length && limit < 200 && <button className="task-index-load" onClick={() => setLimit(Math.min(200, limit + 50))}>Show 50 more records</button>}
    {limit >= 200 && filtered.length > 200 && <p className="task-index-notice">Display limited to 200 records. Refine the filters to inspect other loaded tasks.</p>}
  </section>
}
