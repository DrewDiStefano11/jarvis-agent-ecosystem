import { useState, type FormEvent } from 'react'
import { useRemoteOperator } from '../state/RemoteOperatorStore'

export function RemoteRuntimeControls() {
  const store = useRemoteOperator()
  const [repository, setRepository] = useState(''), [runId, setRunId] = useState('')
  const [baseSha, setBaseSha] = useState(''), [confirmedHash, setConfirmedHash] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const request = async (id: string, action: 'request_pause' | 'resume' | 'request_cancellation') => {
    if (!window.confirm('Send ' + action.replaceAll('_', ' ') + ' for runtime run ' + id + '?')) return
    try { await store.runtimeControl(id, action) } catch { /* shared store reports uncertainty */ }
  }
  const preview = async (event: FormEvent) => {
    event.preventDefault(); setConfirmedHash(''); setPreviewing(true)
    try { await store.previewWorkspace(repository, runId, baseSha) } catch { /* shared store reports failure */ }
    finally { setPreviewing(false) }
  }
  const approve = async (event: FormEvent) => {
    event.preventDefault()
    if (!window.confirm('Approve this exact workspace reservation plan for 15 minutes? This approves no filesystem or code execution.')) return
    try { await store.approveWorkspace(confirmedHash); setConfirmedHash('') } catch { /* require fresh preview after uncertainty */ }
  }
  return <section className="panel"><h2>Runtime and protected actions</h2>
    <p>Visible runs are scoped to the selected goal, or the latest accessible runs. Up to 20 are shown.
      Pause requests await native worker acknowledgement; this interface cannot confirm worker execution.</p>
    {!store.runs && <p>Runtime view unavailable.</p>}
    {store.runs?.items.length === 0 && <p>No accessible runtime runs in this view.</p>}
    <ul className="runtime-list">{store.runs?.items.map(run => {
      const id = run.specification.run_id
      return <li key={id} className="runtime-card" aria-label={'Runtime run ' + id}>
        <h3>{id}</h3><p>Task: {run.specification.task_id} · Agent: {run.specification.agent_id}</p>
        <p>State: <strong>{run.state}</strong> · Version: {run.version} · Recovery: {run.recovery_status}</p>
        <p>Checkpoint: {run.latest_checkpoint_id ?? 'None recorded'}</p>
        {run.failure && <p role="alert">{run.failure.category}: {run.failure.detail}</p>}
        {run.blocking_reason && <p>{run.blocking_reason.code}: {run.blocking_reason.detail}</p>}
        <div className="actions"><button disabled={store.busy || store.authBusy || !!store.error
          || !['queued', 'claimed', 'running'].includes(run.state)}
          onClick={() => void request(id, 'request_pause')}>Request pause</button>
          <button disabled={store.busy || store.authBusy || !!store.error || run.state !== 'paused'}
            onClick={() => void request(id, 'resume')}>Resume run</button>
          <button disabled={store.busy || store.authBusy || !!store.error
            || ['succeeded','failed','cancelled','timed_out','abandoned','cancelling','cancel_requested'].includes(run.state)}
            onClick={() => void request(id, 'request_cancellation')}>Request run cancellation</button>
          <button onClick={() => void store.inspectRun(id)}>Inspect run output</button></div>
      </li>
    })}</ul>
    {store.pendingRuntime && <p role="status">Last runtime request has no acknowledgement.
      Refresh and inspect recorded state before retrying. <button disabled={store.busy || !!store.error || !store.runs?.items.some(run => run.specification.run_id === store.pendingRuntime?.run_id)}
        onClick={() => void request(store.pendingRuntime!.run_id, store.pendingRuntime!.command_type)}>Retry last runtime request</button></p>}
    {store.runtimeEvidence && <details open><summary>Runtime evidence: {store.runtimeEvidence.run.specification.run_id}</summary>
      <p>Inspected native state: {store.runtimeEvidence.run.state}</p><pre>{store.runtimeEvidence.result
        ? JSON.stringify(store.runtimeEvidence.result, null, 2) : 'No model execution result recorded.'}</pre></details>}
    <h3>Review a workspace reservation</h3>
    <p>Enter an operator-registered repository, a readable runtime run and an exact base commit.
      Preview derives the current native plan. Only an independently authorized operator may approve it.</p>
    <form onSubmit={event => void preview(event)}><label>Registered repository ID<input required maxLength={120}
      value={repository} onChange={event => setRepository(event.target.value)}/></label>
      <label>Workspace runtime run ID<input required maxLength={120} value={runId}
        onChange={event => setRunId(event.target.value)}/></label>
      <label>Base commit SHA<input required pattern="[a-f0-9]{40}" maxLength={40} value={baseSha}
        onChange={event => setBaseSha(event.target.value)} autoComplete="off" spellCheck={false}/></label>
      <button disabled={previewing || store.busy || store.authBusy}>{previewing ? 'Reading native plan…' : 'Preview workspace plan'}</button></form>
    {store.workspaceReview && <div className="workspace-review"><h3>Native plan · review every field</h3>
      <pre>{JSON.stringify(store.workspaceReview.plan, null, 2)}</pre>
      <form onSubmit={event => void approve(event)}><label>Retype the exact plan hash<input required
        value={confirmedHash} maxLength={64} autoComplete="off" spellCheck={false}
        onChange={event => setConfirmedHash(event.target.value)}/></label>
        <button disabled={store.busy || store.authBusy || !!store.error
          || confirmedHash !== store.workspaceReview.plan.plan_hash}>Approve workspace reservation</button></form></div>}
    {store.workspaceApproval && <p role="status">Workspace reservation approval recorded:
      {' '}{store.workspaceApproval.approval_id}. Expires {new Date(store.workspaceApproval.expires_at).toLocaleString()}.
      No workspace creation, source change or execution was performed.</p>}
  </section>
}
