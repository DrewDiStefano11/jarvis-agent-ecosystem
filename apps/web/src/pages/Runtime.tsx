import { request } from '../api/client'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ToolExecutionHistory, ToolExecutionPanel } from '../components/ToolExecutionPanel'
import { newPlanningSubmission, submitPlanning, type PlanningSubmission } from '../api/planning'
import { useAppStore } from '../state/AppStore'
import { Status } from '../components/Status'
import { forgetPlanningSubmission, readPlanningRecovery, rememberPlanningSubmission, restorePlanningSubmission, type SavedPlanningSubmission } from '../state/planningRecovery'

export function Runtime() {
  const [searchParams] = useSearchParams()
  const [mode, setMode] = useState<'planning' | 'workspace'>(searchParams.get('mode') === 'workspace' ? 'workspace' : 'planning')
  const { runtime, tools, tasks, system, refresh, selectTask } = useAppStore()
  const { identities, loadIdentities, actorId, selectActor, taskId, setTaskId, runs, executions, refreshRuntime } = runtime
  const [targetId, setTargetId] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<PlanningSubmission | null>(null)
  const [recovery, setRecovery] = useState(readPlanningRecovery)
  const [storageWarning, setStorageWarning] = useState('')
  const worker = system?.autonomousWorker
  useEffect(() => { void loadIdentities().catch(error => setMessage(error instanceof Error ? error.message : 'Cannot load identities')) }, [loadIdentities])
  useEffect(() => {
    const reload = () => setRecovery(readPlanningRecovery())
    window.addEventListener('storage', reload)
    return () => window.removeEventListener('storage', reload)
  }, [])
  const active = identities.filter(identity => identity.lifecycle_state === 'active' && identity.is_enabled)
  const workerActorId = worker?.workerActorId
  const workerActor = active.find(identity => identity.id === workerActorId)
  const task = tasks.find(item => item.id === taskId)
  const identityBlock = !workerActorId
    ? 'Configure JARVIS_AUTONOMOUS_WORKER_ACTOR_ID and restart the local services before queueing a plan.'
    : !workerActor
      ? 'The configured worker identity is unavailable or inactive. Check its identity configuration before queueing a plan.'
      : (pending?.actorId ?? actorId) !== workerActorId
        ? pending
          ? 'The configured worker identity changed. Inspect history and clear this submission before queueing with the current worker.'
          : 'Select the configured worker identity to queue a plan. Other identities remain available for authorized history.'
        : null
  const canQueue = !busy && !identityBlock && Boolean(actorId && targetId && task)
    && Boolean(task && ['queued', 'retrying'].includes(task.status))
    && Boolean(worker?.enabled && worker.providerReady) && !system?.emergencyStop
  const recover = async (saved: SavedPlanningSubmission) => {
    if (busy || pending) return
    const originalTask = tasks.find(item => item.id === saved.taskId)
    if (!originalTask) { setMessage('The saved task is not available. Refresh the Hub or inspect its durable history before creating replacement work.'); return }
    setBusy(true)
    try {
      const restored = await restorePlanningSubmission(saved, originalTask)
      selectActor(restored.actorId)
      setTargetId(restored.targetId)
      setTaskId(originalTask.id)
      setPending(restored)
      setMode(restored.responseFormat === 'workspace_plan_json_v1' ? 'workspace' : 'planning')
      setMessage('Saved submission recovered. Inspect its history, then retry with the original command IDs if needed. Recovery does not queue work automatically.')
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Cannot recover the saved submission') }
    finally { setBusy(false) }
  }
  const forget = (id: string) => {
    const warning = forgetPlanningSubmission(id)
    setStorageWarning(warning)
    setRecovery(readPlanningRecovery())
    if (pending?.id === id) setPending(null)
    setMessage(warning ? 'The form was cleared, but its saved retry ID remains in browser storage.' : 'Saved retry ID forgotten. Existing work remains in durable history; this does not cancel a task or run.')
  }
  const prepare = async () => {
    if (!task || busy) return
    setBusy(true)
    setMessage('')
    try {
      const result = await request<{ actorId: string; workerActorConfigured: boolean }>('/api/local-planning/setup', { method: 'POST', body: JSON.stringify({ taskId: task.id }) })
      await Promise.all([loadIdentities(), refresh()])
      selectActor(result.actorId)
      setTargetId(result.actorId)
      setMessage(result.workerActorConfigured ? 'Local planner prepared for this task. Queue the plan when ready.' : `Local planner prepared. Set JARVIS_AUTONOMOUS_WORKER_ACTOR_ID=${result.actorId} in the local worker configuration, then start the configured worker.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Local planner setup failed')
    } finally { setBusy(false) }
  }
  const queue = async () => {
    if (!task || !actorId || !targetId || !canQueue) return
    setBusy(true)
    const submission = pending ?? newPlanningSubmission(task, actorId, targetId, mode)
    setPending(submission)
    try {
      setStorageWarning(await rememberPlanningSubmission(submission))
      setRecovery(readPlanningRecovery())
      const run = await submitPlanning(submission)
      setMessage(`Queued ${run.specification.run_id}. The configured local worker will claim eligible work.`)
      setPending(null)
      setStorageWarning(forgetPlanningSubmission(submission.id))
      setRecovery(readPlanningRecovery())
      try { await Promise.all([refresh(), refreshRuntime()]) }
      catch { setMessage(`Queue acknowledged for ${run.specification.run_id}. Status refresh failed; refresh runtime history to inspect progress.`) }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Planning submission failed')
    } finally { setBusy(false) }
  }
  return <>
    <header className="page-title"><div><p className="eyebrow">Local autonomous execution</p><h1>Planning workspace</h1><p>Queue a bounded model-assisted plan, follow its review, and inspect durable results.</p></div></header>
    <section className="panel"><h2>Worker readiness</h2><Status value={worker?.status ?? 'unknown'}/>
      <p>{worker?.enabled ? 'Local worker enabled' : 'Worker disabled — enable it using the local setup guide.'} · {worker?.providerReady ? 'Provider configured' : 'Provider not configured'}</p>
      <p>Configured worker identity: {workerActor?.display_name ?? workerActorId ?? 'Not configured'}{workerActor && <> · <code>{workerActor.id}</code></>}</p>
      {worker?.reasonCode && <p role="status">{worker.reasonCode}</p>}
      <dl className="details-grid"><div><dt>Queued eligible</dt><dd>{worker?.queuedEligibleRuntimeCount ?? '—'}</dd></div><div><dt>Executing</dt><dd>{worker?.activeExecutionCount ?? '—'}</dd></div><div><dt>Completed</dt><dd>{worker?.completedExecutionCount ?? '—'}</dd></div><div><dt>Needs review</dt><dd>{worker?.reviewRequiredCount ?? '—'}</dd></div></dl>
      <p className="muted">Planning produces a proposal. Workspace plans require separate authorization of exact actions and file contents before tools can run. Provider readiness describes configuration; successful inference appears in execution history.</p>
    </section>
    <section className="panel"><h2>Queue a local plan</h2>
      {(storageWarning || recovery.warning) && <p role="alert">{storageWarning || recovery.warning}</p>}
      {recovery.items.some(item => item.id !== pending?.id) && <div className="callout"><h3>Unfinished submissions saved in this browser</h3><p>Only retry IDs and an input fingerprint are stored here. Task text and results stay in the Hub. Recovering a form never queues it automatically.</p>{recovery.items.filter(item => item.id !== pending?.id).map(saved => <article key={saved.id}><p>{tasks.find(item => item.id === saved.taskId)?.title ?? saved.taskId} · {new Date(saved.timestamp).toLocaleString()}</p><p>Run ID: <code>run-{saved.id}</code></p><button className="secondary" disabled={busy || Boolean(pending)} onClick={() => void recover(saved)}>Recover submission</button> <button className="secondary" disabled={busy} onClick={() => forget(saved.id)}>Forget saved retry ID</button></article>)}</div>}
      <p>Queue as the configured worker identity, with its existing task permissions, or explicitly prepare it for the selected task. The target agent can be a different active identity. <Link to="/tasks">Create a task</Link> first.</p>
      <label>Plan type<select value={mode} disabled={busy || Boolean(pending)} onChange={event => setMode(event.target.value as 'planning' | 'workspace')}><option value="planning">Planning advice</option><option value="workspace">Workspace actions and report</option></select></label>
      <div className="filters"><label>Act as local identity<select value={actorId} disabled={busy || Boolean(pending)} onChange={event => selectActor(event.target.value)}><option value="">Select identity</option>{active.map(identity => <option key={identity.id} value={identity.id}>{identity.display_name}</option>)}</select></label>
      <label>Target agent<select value={targetId} disabled={busy || Boolean(pending)} onChange={event => setTargetId(event.target.value)}><option value="">Select target</option>{active.map(identity => <option key={identity.id} value={identity.id}>{identity.display_name}</option>)}</select></label>
      <label>Task and history<select value={taskId} disabled={busy || Boolean(pending)} onChange={event => setTaskId(event.target.value)}><option value="">Select task</option>{tasks.map(item => <option key={item.id} value={item.id}>{item.title} · {item.status}</option>)}</select></label></div>
      {!active.length && <p>No active identities found. Select a task and prepare its local planner below.</p>}
      {identityBlock && <p role="status">{identityBlock}</p>}
      {pending && task && !['queued', 'retrying'].includes(task.status) && <p role="status">This task is now {task.status}. Its saved submission cannot queue further work. Inspect runtime history and clear the form after confirming the original outcome.</p>}
      {workerActor && actorId !== workerActor.id && <p><button className="secondary" disabled={busy || Boolean(pending)} onClick={() => selectActor(workerActor.id)}>Use configured worker identity</button></p>}
      {task && <blockquote>{task.description}</blockquote>}
      {task && ['under_review', 'failed', 'cancelled', 'completed'].includes(task.status) && <p><Link to={`/tasks?correct=${encodeURIComponent(task.id)}`}>Revise task input</Link> to create a linked follow-up, then explicitly prepare and queue its plan. The original result stays in history.</p>}
      <p><button className="secondary" disabled={busy || Boolean(pending) || !task || !['queued', 'retrying'].includes(task.status)} onClick={() => void prepare()}>Prepare local planner for this task</button></p>
      <p className="muted">This explicit setup grants runtime permissions for only this task. It preserves existing denials and never grants tools or enables a model.</p>
      <button className="primary" disabled={!canQueue} onClick={() => void queue()}>{busy ? 'Submitting…' : pending ? 'Retry same submission' : 'Queue local plan'}</button>
      {pending && !busy && <><p>Retry reuses the same context and command IDs, including after recovering this form following a reload. Inspect history before starting different work.</p><button className="secondary" onClick={() => forget(pending.id)}>Clear submission form</button></>}
      {message && <p role="status">{message}</p>}
    </section>
    <section className="panel"><h2>Runtime history</h2><button className="secondary" disabled={!actorId || runtime.loading} onClick={() => void refreshRuntime()}>Refresh runtime</button>
      {runtime.error && <p role="alert">{runtime.error}</p>}
      {!actorId && <p>Select a local identity to read authorized history.</p>}
      {runs.map(run => <article className="runtime-result" key={run.specification.run_id}><h3>{run.specification.requested_operation}</h3><Status value={run.state}/><p>{run.status_detail}</p>{run.state==='paused'&&<p>Execution is paused for operator review. Inspect the result or failure code before deciding on further work.</p>}<p>{run.attempt_count} attempt(s) · <code>{run.specification.run_id}</code></p><button className="secondary" onClick={() => selectTask(run.specification.task_id)}>Open task</button></article>)}
      {actorId && !runtime.loading && !runtime.error && !runs.length && <p>No authorized runs in this selection.</p>}
      {runtime.nextOffset !== null && <p>Showing the first 50 runs. Select a task to narrow history.</p>}
    </section>
    <section className="panel"><h2>Persisted model results</h2>{!taskId && <p>Select a task to inspect its results.</p>}
      {executions.map(execution => <article className="runtime-result" key={execution.executionId}><h3>{execution.result?.summary ?? 'Execution in progress'}</h3><Status value={execution.stage}/><p>{execution.provider ?? 'Provider pending'} · {execution.model ?? 'Model pending'} · {execution.requestCount} request(s)</p><code>{execution.executionId}</code>
        {execution.failureCode && <p role="status">{execution.failureCode}</p>}
        {execution.result && <><p style={{ whiteSpace: 'pre-wrap' }}>{execution.result.analysis}</p><h4>Recommendations</h4><ul>{execution.result.recommendations.map((item, index) => <li key={index}><strong>{item.title}</strong> ({item.priority}) — {item.description}</li>)}</ul><h4>Risks</h4><ul>{execution.result.risks.map((item, index) => <li key={index}><strong>{item.title}</strong> ({item.severity}) — {item.description}<p>Mitigation: {item.mitigation}</p></li>)}</ul>
          <h4>Assumptions</h4><ul>{execution.result.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul><h4>Missing information</h4><ul>{execution.result.missingInformation.map((item, index) => <li key={index}>{item}</li>)}</ul>{execution.result.requiresHumanReview && <p role="status">Human review required; the model cannot approve its own work.</p>}</>}
        {execution.result?.steps && <ToolExecutionPanel key={`${actorId}:${execution.executionId}:${execution.resultHash}`} execution={execution}/>}
      </article>)}
      {taskId && actorId && !runtime.loading && !runtime.error && !executions.length && <p>No persisted model execution for this task yet.</p>}
    </section>
    {actorId && taskId && (mode === 'workspace' || tools.executions.length > 0) && <ToolExecutionHistory/>}
  </>
}
