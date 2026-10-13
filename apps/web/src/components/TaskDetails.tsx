import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useAppStore } from '../state/AppStore'
import { ActionDrawer } from './ActionDrawer'
import { NavIcon } from './NavIcon'
import { Progress, Status } from './Status'
import { PlannedWork } from './PlannedWork'
import { CoordinatedWork } from './CoordinatedWork'
import { TaskCreateForm } from './TaskCreateForm'
import '../styles/task-details.css'

const Planning = lazy(() => import('../pages/Runtime').then(module => ({ default: module.Runtime })))
const ApprovalInbox = lazy(() => import('../pages/Approvals').then(module => ({ default: module.Approvals })))
type DetailView = 'summary' | 'planning' | 'correction' | 'creation' | 'approvals'

export function TaskDetails() {
  const { tasks, agents, approvals, artifacts, auditEvents, selectedTaskId, selectTask, runtime, action, decomposition, coordination, system, loading, error, connection, resyncRequired, refresh } = useAppStore()
  const [view, setView] = useState<{ taskId: string | null; tab: DetailView }>({ taskId: selectedTaskId, tab: 'summary' })
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<{ taskId: string; message: string } | null>(null)
  const [creationWarning, setCreationWarning] = useState('')
  const content = useRef<HTMLDivElement>(null)
  const previousView = useRef(`${selectedTaskId}:summary`)
  const task = tasks.find(item => item.id === selectedTaskId)
  const tab = view.taskId === selectedTaskId ? view.tab : 'summary'
  useEffect(() => {
    const signature = `${selectedTaskId}:${tab}`
    if (previousView.current === signature) return
    previousView.current = signature
    const target = content.current?.querySelector<HTMLElement>('input[name=title]:not(:disabled)') ?? content.current?.querySelector<HTMLElement>('.task-detail-navigation button[aria-pressed=true], .task-detail-navigation button')
    target?.focus()
  }, [selectedTaskId, tab])
  const show = (next: DetailView) => setView({ taskId: selectedTaskId, tab: next })
  const close = () => selectTask(null)
  const stale = Boolean(loading || !system || error || resyncRequired || connection !== 'connected')
  if (!task) return <ActionDrawer open title="Task unavailable" closeLabel="Close task details" onClose={close}><p>The selected task is not in the loaded snapshot. Refresh to confirm its availability.</p><button className="secondary" onClick={() => void refresh()}>Refresh state</button></ActionDrawer>
  const children = tasks.filter(item => item.parentTaskId === task.id)
  const timeline = auditEvents.filter(event => event.taskId === task.id).sort((a, b) => b.sequenceNumber - a.sequenceNumber).slice(0, 20)
  const relatedApprovals = approvals.filter(approval => task.approvalIds.includes(approval.id))
  const pending = relatedApprovals.filter(approval => approval.status === 'pending')
  const closed = ['completed', 'failed', 'cancelled'].includes(task.status)
  const correctable = ['under_review', 'failed', 'cancelled', 'completed'].includes(task.status)
  const plan = () => { runtime.setTaskId(task.id); show('planning') }
  const inspect = (id: string) => { selectTask(id); setView({ taskId: id, tab: 'summary' }) }
  const cancel = async () => {
    if (cancelling || stale || closed) return
    setCancelling(true); setCancelError(null)
    try { await action(`/api/tasks/${encodeURIComponent(task.id)}/cancel`) }
    catch (failure) { setCancelError({ taskId: task.id, message: failure instanceof Error ? failure.message : 'Cancellation failed' }) }
    finally { setCancelling(false) }
  }
  const assignedName = (id: string) => runtime.identities.find(identity => identity.id === id)?.display_name ?? agents.find(agent => agent.id === id)?.name ?? id
  return <ActionDrawer open title={task.title} closeLabel="Close task details" onClose={close}>
    <div ref={content} className="task-detail-workspace">
      <div className="task-detail-navigation"><button className="secondary" aria-pressed={tab === 'summary'} onClick={() => show('summary')}><NavIcon name="tasks"/>Task summary</button><button className="secondary" aria-pressed={tab === 'planning'} onClick={plan}><NavIcon name="planning"/>Planning & runtime</button>{relatedApprovals.length > 0 && <button className="secondary" aria-pressed={tab === 'approvals'} onClick={() => show('approvals')}><NavIcon name="attention"/>Approvals {pending.length > 0 ? `(${pending.length} pending)` : ''}</button>}</div>
      {tab === 'planning' ? <Suspense fallback={<p role="status">Loading planning...</p>}><Planning lockedTaskId={task.id} onInspectTask={inspect} onCreateTask={() => show('creation')} onCorrectTask={id => { selectTask(id); setView({ taskId: id, tab: 'correction' }) }}/></Suspense> : tab === 'approvals' ? <Suspense fallback={<p role="status">Loading approvals...</p>}><ApprovalInbox key={task.id} lockedTaskId={task.id} onInspectTask={inspect} onOpenPlanning={plan}/></Suspense> : tab === 'correction' || tab === 'creation' ? <>
        <h2>{tab === 'correction' ? 'Revise the request' : 'Create a new request'}</h2>{stale && <p role="status">Reconnect and refresh before creating work.</p>}{tab === 'correction' && !correctable ? <p>This task is still active. Inspect its progress or cancel it before creating a correction.</p> : <fieldset disabled={stale}><TaskCreateForm key={`${task.id}:${tab}`} source={tab === 'correction' ? task : undefined} onCreated={(created, warning) => { setCreationWarning(warning); inspect(created.id) }}/></fieldset>}
      </> : <>
        {creationWarning && <p role="status">{creationWarning}</p>}
        <div className="task-detail-title"><span className={`task-detail-emblem emblem-${task.status}`}><NavIcon name={task.error ? 'attention' : task.status === 'completed' ? 'approvals' : 'tasks'}/></span><div><p className="task-detail-kicker">Task · {task.priority} priority</p><h2>{task.title}</h2><Status value={task.status}/></div></div>
        <Progress value={task.progress} label="Reported task progress"/>
        <p className="task-detail-status">{task.statusMessage}</p>
        {stale && <p className="task-detail-notice" role="status">Last-known task snapshot. Refresh before changing its state.</p>}
        <div className="task-detail-actions"><button className="primary" onClick={plan}><NavIcon name="planning"/>{task.result !== null ? 'Inspect runtime evidence' : 'Plan this task'}</button>{correctable && <button className="secondary" onClick={() => show('correction')}>Revise task input</button>}{pending.length > 0 && <button className="secondary" onClick={() => show('approvals')}>Review {pending.length} pending approval records</button>}</div>
        <section className="task-detail-section"><h3>Original request</h3><p className="task-original-request">{task.request}</p>{task.correctionOfTaskId && <p className="muted">Corrected follow-up to <button className="link-button" onClick={() => inspect(task.correctionOfTaskId!)}>{tasks.find(item => item.id === task.correctionOfTaskId)?.title ?? task.correctionOfTaskId}</button></p>}{tasks.some(item => item.correctionOfTaskId === task.id) && <><h4>Corrected follow-ups</h4><ul>{tasks.filter(item => item.correctionOfTaskId === task.id).map(item => <li key={item.id}><button className="link-button" onClick={() => inspect(item.id)}>{item.title}</button> · {item.status}</li>)}</ul></>}</section>
        {task.error && <div className="task-detail-notice task-detail-error"><NavIcon name="attention"/><div><strong>{task.error.code}</strong><p>{task.error.message}</p></div></div>}
        {task.result !== null && <section className="task-detail-section saved-task-result"><div className="task-detail-section-heading"><NavIcon name="approvals"/><h3>Final result</h3></div><p className="muted">Saved task evidence may include demonstration results. Inspect runtime evidence to verify model execution.</p><pre>{task.result}</pre></section>}
        <section className="task-detail-section"><h3>Assignment and blockers</h3><dl className="task-detail-facts"><div><dt>Manager</dt><dd>{task.assignedManagerId ? assignedName(task.assignedManagerId) : 'Unassigned'}</dd></div><div><dt>Specialists</dt><dd>{task.assignedAgentIds.map(assignedName).join(', ') || 'None'}</dd></div><div><dt>Team status</dt><dd>{task.teamSelection?.status || 'Not started'}</dd></div><div><dt>Retries</dt><dd>{task.retryCount} / {task.maxRetries}</dd></div><div><dt>Blockers</dt><dd>{task.blockedBy.length ? task.blockedBy.map(id => <button key={id} className="link-button" disabled={!tasks.some(item => item.id === id)} onClick={() => inspect(id)}>{tasks.find(item => item.id === id)?.title ?? id}</button>) : 'None'}</dd></div></dl><p className="muted">Assignment names do not prove model execution. Demonstration agents and registered identities have separate runtimes.</p></section>
        <section className="task-detail-section"><PlannedWork record={decomposition.record} error={decomposition.error} execution={coordination.record}/><CoordinatedWork record={coordination.record} error={coordination.error}/></section>
        <section className="task-detail-section"><h3>Child tasks</h3>{children.length ? <ul className="task-detail-children">{children.map(child => <li key={child.id}><button className="link-button" onClick={() => inspect(child.id)}>{child.title}</button><Status value={child.status}/></li>)}</ul> : <p className="muted">No child tasks.</p>}</section>
        <section className="task-detail-section"><h3>Approvals</h3>{relatedApprovals.length ? relatedApprovals.map(approval => <div className="task-detail-approval" key={approval.id}><span>{approval.title}</span><Status value={approval.status}/></div>) : <p className="muted">No approval records.</p>}</section>
        <section className="task-detail-section"><h3>Artifacts</h3>{task.artifactIds.map(id => { const artifact = artifacts.find(item => item.id === id); return artifact ? <div className="task-detail-artifact" key={id}><NavIcon name="tasks"/><div><strong>{artifact.name}</strong><p>{artifact.summary}</p><small>Recorded simulated reference</small><code>{artifact.simulatedPath}</code></div></div> : <p className="muted" key={id}>Artifact {id} is not in the loaded snapshot.</p> })}{!task.artifactIds.length && <p className="muted">No artifacts yet.</p>}</section>
        <section className="task-detail-section"><h3>Timeline</h3>{timeline.length ? <ol className="task-evidence-timeline">{timeline.map(event => <li key={event.id}><span aria-hidden="true"/><div><strong>{event.summary}</strong><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleString()}</time><details><summary>Event record</summary><small>Sequence {event.sequenceNumber} · {event.correlationId}</small></details></div></li>)}</ol> : <p className="muted">No task events yet.</p>}<p className="muted">Up to 20 loaded events for this task, newest first.</p></section>
        <details className="task-detail-section task-technical-details"><summary>Record and team details</summary><dl className="task-detail-facts"><div><dt>Task ID</dt><dd><code>{task.id}</code></dd></div><div><dt>Created</dt><dd>{new Date(task.createdAt).toLocaleString()}</dd></div><div><dt>Updated</dt><dd>{new Date(task.updatedAt).toLocaleString()}</dd></div>{task.completedAt && <div><dt>Completed</dt><dd>{new Date(task.completedAt).toLocaleString()}</dd></div>}</dl>{task.teamSelection && <><h4>Required capabilities</h4><ul>{task.teamSelection.requiredCapabilities.map(capability => <li key={capability}>{capability}</li>)}</ul>{task.teamSelection.optionalCapabilities.length > 0 && <><h4>Optional capabilities</h4><ul>{task.teamSelection.optionalCapabilities.map(capability => <li key={capability}>{capability}</li>)}</ul></>}<h4>Team rationale</h4><ul>{task.teamSelection.rationaleSummaries.map(reason => <li key={reason.agentId}><strong>{assignedName(reason.agentId)}:</strong> {reason.rationale}</li>)}</ul></>}</details>
        <div className="task-detail-cancel"><p>{closed ? 'This task is closed. Its saved evidence remains available.' : 'Cancel this request when its work is no longer needed.'}</p><button className="danger-button" disabled={cancelling || stale || closed} onClick={() => void cancel()}>{cancelling ? 'Cancelling...' : 'Cancel task'}</button></div>{cancelError?.taskId === task.id && <p role="alert">{cancelError.message}</p>}
      </>}
    </div>
  </ActionDrawer>
}
