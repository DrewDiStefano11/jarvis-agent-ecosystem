import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { PlanningOutput } from '../components/PlanningOutput'
import { useAppStore } from '../state/AppStore'
import type { ResultReviewItem, ReviewDecision } from '../types/results'
import '../styles/results.css'

function ReviewCard({ item }: { item: ResultReviewItem }) {
  const { results, system, selectTask, refresh } = useAppStore()
  const [reason, setReason] = useState('')
  const [revision, setRevision] = useState('')
  const [changes, setChanges] = useState(false)
  const execution = item.execution
  const disabled = results.busy || Boolean(results.pending) || !system || system.emergencyStop
  const decide = async (decision: ReviewDecision) => {
    try { await results.decide(item, decision, reason, decision === 'request_changes' ? revision : undefined); await refresh() }
    catch { /* Shared state preserves command and acknowledgement status. */ }
  }
  return <article className="result-review-card" aria-label={`Review ${item.taskTitle}`}>
    <header><div><span className="result-status">Awaiting your review</span><h2>{item.taskTitle}</h2></div><button className="secondary" onClick={() => selectTask(execution.taskId)}>Open task</button></header>
    {execution.result && <PlanningOutput result={execution.result}/>}
    <details className="result-provenance"><summary>Generation evidence</summary><dl><div><dt>Provider / model</dt><dd>{execution.provider ?? 'Not recorded'} / {execution.model ?? 'Not recorded'}</dd></div><div><dt>Execution</dt><dd>{execution.executionId}</dd></div><div><dt>Original result SHA-256</dt><dd>{execution.resultHash}</dd></div><div><dt>Runtime checkpoint</dt><dd>{item.checkpointId} · version {item.runVersion}</dd></div><div><dt>Generated</dt><dd>{execution.completedAt ? new Date(execution.completedAt).toLocaleString() : 'Time not recorded'}</dd></div></dl><p>The original model response requested human review. Your decision is recorded separately and preserves that evidence.</p></details>
    <div className="result-decision"><label>Review note<textarea value={reason} maxLength={2000} onChange={event => setReason(event.target.value)} disabled={disabled} placeholder="Record what you checked and why you made this decision."/></label>
      {changes && <label>Full revised request<textarea aria-label="Full revised request" value={revision} maxLength={2000} onChange={event => setRevision(event.target.value)} disabled={disabled} placeholder="Describe the complete revised outcome and constraints for the new draft."/><small>A new correction draft will be created. Planning is queued separately.</small></label>}
      <div className="result-actions"><button className="primary" disabled={disabled || reason.trim().length < 3} onClick={() => void decide('accept')}>Accept result</button>{changes ? <button className="secondary" disabled={disabled || reason.trim().length < 3 || revision.trim().length < 3} onClick={() => void decide('request_changes')}>Create correction draft</button> : <button className="secondary" disabled={disabled} onClick={() => setChanges(true)}>Request changes</button>}<button className="result-reject" disabled={disabled || reason.trim().length < 3} onClick={() => void decide('reject')}>Reject result</button></div>
    </div>
  </article>
}

export function Inbox() {
  const { results, system, selectTask, refresh } = useAppStore()
  const { refresh: refreshResults } = results
  useEffect(() => { void refreshResults() }, [refreshResults])
  return <div className="results-workspace"><header className="results-heading"><div><p className="eyebrow">Your decisions</p><h1>Inbox</h1><p>Read the saved output, check its assumptions, and decide what happens next.</p></div><button className="secondary" disabled={results.loading || results.busy} onClick={() => void refreshResults()}>{results.loading ? 'Refreshing…' : 'Refresh results'}</button></header>
    {results.error && <p className="result-error" role="alert">{results.error}</p>}
    {!results.actorId && !results.loading && <section className="reviewer-setup"><h2>Enable local result review</h2><p>Create a dedicated local reviewer with permission to read and decide saved results. This grants no model, workspace or execution permission.</p><button className="primary" disabled={results.busy} onClick={() => void results.setup()}>Enable local result review</button></section>}
    {system?.emergencyStop && <p className="result-error" role="status">Emergency stop is active. Review decisions are blocked.</p>}
    {results.pending && <section className="result-pending" role="status"><h2>Decision acknowledgement pending</h2><p>Your captured {results.pending.decision.replaceAll('_', ' ')} decision for {results.pending.execution_id} is retained. Retry sends the exact same command.</p><button disabled={results.busy || Boolean(system?.emergencyStop)} onClick={async () => { try { await results.retry(); await refresh() } catch { /* Error remains visible. */ } }}>Retry captured decision</button></section>}
    {results.receipt && <section className="result-receipt" role="status"><h2>{results.receipt.decision === 'accept' ? 'Result accepted' : results.receipt.decision === 'request_changes' ? 'Correction draft created' : 'Result rejected'}</h2><p>Decision saved {new Date(results.receipt.reviewedAt).toLocaleString()}.</p><div>{results.receipt.artifactId && <Link to="/library">View accepted output in Library →</Link>}{results.receipt.correctionTaskId && <button className="secondary" onClick={() => selectTask(results.receipt!.correctionTaskId!)}>Open correction draft</button>}</div></section>}
    {results.actorId && !results.loading && !results.items.length && !results.error && <section className="results-empty"><h2>You’re up to date</h2><p>No saved planning results are currently awaiting your review.</p><Link to="/work">View work →</Link></section>}
    <div className="result-review-list">{results.items.map(item => <ReviewCard key={item.execution.executionId} item={item}/>)}</div>
    <footer className="results-footer"><span>Action authorization has its own review flow.</span><Link to="/approvals">Open action approval records →</Link></footer>
  </div>
}
