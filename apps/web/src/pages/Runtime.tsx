import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { newPlanningSubmission, submitPlanning, type PlanningSubmission } from '../api/planning'
import { useAppStore } from '../state/AppStore'
import { Status } from '../components/Status'

export function Runtime() {
  const { runtime, tasks, system, refresh, selectTask } = useAppStore()
  const { identities, loadIdentities, actorId, selectActor, taskId, setTaskId, runs, executions, refreshRuntime } = runtime
  const [targetId, setTargetId] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<PlanningSubmission | null>(null)
  const worker = system?.autonomousWorker
  useEffect(() => { void loadIdentities().catch(error => setMessage(error instanceof Error ? error.message : 'Cannot load identities')) }, [loadIdentities])
  const active = identities.filter(identity => identity.lifecycle_state === 'active' && identity.is_enabled)
  const task = tasks.find(item => item.id === taskId)
  const queue = async () => {
    if (!task || !actorId || !targetId || busy) return
    setBusy(true)
    const submission = pending ?? newPlanningSubmission(task, actorId, targetId)
    setPending(submission)
    try {
      const run = await submitPlanning(submission)
      setMessage(`Queued ${run.specification.run_id}. The configured local worker will claim eligible work.`)
      setPending(null)
      await refresh()
      await refreshRuntime()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Planning submission failed')
    } finally { setBusy(false) }
  }
  return <>
    <header className="page-title"><div><p className="eyebrow">Local autonomous execution</p><h1>Planning workspace</h1><p>Queue a bounded model-assisted plan, follow its review, and inspect durable results.</p></div></header>
    <section className="panel"><h2>Worker readiness</h2><Status value={worker?.status ?? 'unknown'}/>
      <p>{worker?.enabled ? 'Local worker enabled' : 'Worker disabled — enable it using the local setup guide.'} · {worker?.providerReady ? 'Provider configured' : 'Provider not configured'}</p>
      {worker?.reasonCode && <p role="status">{worker.reasonCode}</p>}
      <dl className="details-grid"><div><dt>Queued eligible</dt><dd>{worker?.queuedEligibleRuntimeCount ?? '—'}</dd></div><div><dt>Executing</dt><dd>{worker?.activeExecutionCount ?? '—'}</dd></div><div><dt>Completed</dt><dd>{worker?.completedExecutionCount ?? '—'}</dd></div><div><dt>Needs review</dt><dd>{worker?.reviewRequiredCount ?? '—'}</dd></div></dl>
      <p className="muted">Planning produces advice. It cannot run tools, modify files or take external actions. Provider readiness describes configuration; a successful inference is verified in execution history.</p>
    </section>
    <section className="panel"><h2>Queue a local plan</h2>
      <p>Select identities already provisioned with runtime permissions. This page does not grant itself permissions. <Link to="/tasks">Create a task</Link> first.</p>
      <div className="filters"><label>Act as local identity<select value={actorId} disabled={busy || Boolean(pending)} onChange={event => selectActor(event.target.value)}><option value="">Select identity</option>{active.map(identity => <option key={identity.id} value={identity.id}>{identity.display_name}</option>)}</select></label>
      <label>Target agent<select value={targetId} disabled={busy || Boolean(pending)} onChange={event => setTargetId(event.target.value)}><option value="">Select target</option>{active.map(identity => <option key={identity.id} value={identity.id}>{identity.display_name}</option>)}</select></label>
      <label>Task and history<select value={taskId} disabled={busy || Boolean(pending)} onChange={event => setTaskId(event.target.value)}><option value="">Select task</option>{tasks.map(item => <option key={item.id} value={item.id}>{item.title} · {item.status}</option>)}</select></label></div>
      {!active.length && <p>No active identities found. Provision a local worker identity using the setup guide before queueing.</p>}
      {task && <blockquote>{task.description}</blockquote>}
      <button className="primary" disabled={busy || !actorId || !targetId || !task || (!pending && !['queued', 'retrying'].includes(task.status)) || !worker?.enabled || !worker.providerReady || Boolean(system?.emergencyStop)} onClick={() => void queue()}>{busy ? 'Submitting…' : pending ? 'Retry same submission' : 'Queue local plan'}</button>
      {pending && !busy && <><p>Retry reuses the same context and command IDs. Inspect history before starting different work.</p><button onClick={() => { setPending(null); setMessage('Form cleared. Any already-created runtime remains in history; clearing the form does not cancel work.') }}>Clear submission form</button></>}
      {message && <p role="status">{message}</p>}
    </section>
    <section className="panel"><h2>Runtime history</h2><button disabled={!actorId || runtime.loading} onClick={() => void refreshRuntime()}>Refresh runtime</button>
      {runtime.error && <p role="alert">{runtime.error}</p>}
      {!actorId && <p>Select a local identity to read authorized history.</p>}
      {runs.map(run => <article className="runtime-result" key={run.specification.run_id}><h3>{run.specification.requested_operation}</h3><Status value={run.state}/><p>{run.status_detail}</p>{run.state==='paused'&&<p>Execution is paused for operator review. Inspect the result or failure code before deciding on further work.</p>}<p>{run.attempt_count} attempt(s) · <code>{run.specification.run_id}</code></p><button onClick={() => selectTask(run.specification.task_id)}>Open task</button></article>)}
      {actorId && !runtime.loading && !runtime.error && !runs.length && <p>No authorized runs in this selection.</p>}
      {runtime.nextOffset !== null && <p>Showing the first 50 runs. Select a task to narrow history.</p>}
    </section>
    <section className="panel"><h2>Persisted model results</h2>{!taskId && <p>Select a task to inspect its results.</p>}
      {executions.map(execution => <article className="runtime-result" key={execution.executionId}><h3>{execution.result?.summary ?? 'Execution in progress'}</h3><Status value={execution.stage}/><p>{execution.provider ?? 'Provider pending'} · {execution.model ?? 'Model pending'} · {execution.requestCount} request(s)</p><code>{execution.executionId}</code>
        {execution.failureCode && <p role="status">{execution.failureCode}</p>}
        {execution.result && <><p style={{ whiteSpace: 'pre-wrap' }}>{execution.result.analysis}</p><h4>Recommendations</h4><ul>{execution.result.recommendations.map((item, index) => <li key={index}><strong>{item.title}</strong> ({item.priority}) — {item.description}</li>)}</ul><h4>Risks</h4><ul>{execution.result.risks.map((item, index) => <li key={index}><strong>{item.title}</strong> ({item.severity}) — {item.description}<p>Mitigation: {item.mitigation}</p></li>)}</ul>
          <h4>Assumptions</h4><ul>{execution.result.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul><h4>Missing information</h4><ul>{execution.result.missingInformation.map((item, index) => <li key={index}>{item}</li>)}</ul>{execution.result.requiresHumanReview && <p role="status">Human review required; the model cannot approve its own work.</p>}</>}
      </article>)}
      {taskId && actorId && !runtime.loading && !runtime.error && !executions.length && <p>No persisted model execution for this task yet.</p>}
    </section>
  </>
}
