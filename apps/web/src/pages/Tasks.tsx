import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import { Empty, Progress, Status } from '../components/Status'
import { TaskCreateForm } from '../components/TaskCreateForm'

export function Tasks() {
  const { tasks, agents, selectTask, runtime, loading } = useAppStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const correctionId = searchParams.get('correct')
  const source = tasks.find(task => task.id === correctionId)
  const canCorrect = source && ['under_review', 'failed', 'cancelled', 'completed'].includes(source.status)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [priority, setPriority] = useState('all')
  const [creating, setCreating] = useState(false)
  const [createdId, setCreatedId] = useState('')
  const [warning, setWarning] = useState('')
  const roots = tasks.filter(task => !task.parentTaskId && task.title.toLowerCase().includes(search.toLowerCase())
    && (status === 'all' || task.status === status) && (priority === 'all' || task.priority === priority))
  return <>
    <header className="page-title"><div><p className="eyebrow">Work orchestration</p><h1>Tasks</h1><p>Trace requests from assignment through audited delivery.</p></div><button className="primary" onClick={() => { setCreating(!creating); setSearchParams({}); setCreatedId('') }}>+ New task</button></header>
    {correctionId && !loading && !canCorrect && <p role="alert">{source ? 'This task is still active. Inspect its progress or cancel it before creating a correction.' : 'The source task is unavailable. Refresh the Hub and inspect task history.'}</p>}
    {(creating || Boolean(canCorrect)) && <TaskCreateForm key={source?.id ?? 'new'} source={canCorrect ? source : undefined} onCreated={(task, storageWarning) => { setCreatedId(task.id); setWarning(storageWarning); setCreating(false); setSearchParams({}) }}/>}
    {createdId && <div className="panel" role="status"><p>Task created and queued. <Link to="/runtime" onClick={() => runtime.setTaskId(createdId)}>Open planning for this task</Link></p>{warning && <p>{warning}</p>}</div>}
    <div className="filters"><label>Search<input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search tasks"/></label><label>Status<select value={status} onChange={event => setStatus(event.target.value)}><option value="all">All</option>{[...new Set(tasks.map(task => task.status))].map(value => <option key={value}>{value}</option>)}</select></label><label>Priority<select value={priority} onChange={event => setPriority(event.target.value)}><option value="all">All</option>{['urgent', 'high', 'medium', 'low'].map(value => <option key={value}>{value}</option>)}</select></label></div>
    <section className="task-list">{roots.map(task => <article className="task-card" key={task.id}>
      <button className="card-hit" onClick={() => selectTask(task.id)} aria-label={`Open ${task.title}`}/><div className="task-top"><div><p className="eyebrow">{task.priority} priority</p><h2>{task.title}</h2></div><Status value={task.status}/></div><p>{task.statusMessage}</p><Progress value={task.progress}/>
      <div className="task-meta"><span>{task.assignedAgentIds.map(id => agents.find(agent => agent.id === id)?.name).filter(Boolean).join(', ') || 'Awaiting assignment'}</span><span>{task.childTaskIds.length} subtasks</span>{task.correctionOfTaskId && <span>Corrected follow-up</span>}{task.blockedBy.length > 0 && <span>⚠ Blocked</span>}{task.approvalIds.length > 0 && <span>◇ Approval</span>}{task.error && <span>! Failure</span>}</div>
      {task.childTaskIds.length > 0 && <div className="children">{task.childTaskIds.map(id => { const child = tasks.find(item => item.id === id); return child ? <button key={id} onClick={() => selectTask(id)}><Status value={child.status}/>{child.title}</button> : null })}</div>}
    </article>)}{!roots.length && <Empty>No tasks match these filters.</Empty>}</section>
  </>
}
