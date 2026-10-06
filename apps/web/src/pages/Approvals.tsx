import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useAppStore } from '../state/AppStore'
import type { Approval } from '../types/contracts'
import '../styles/approvals.css'

export function Approvals() {
  const { approvals, agents, tasks, system, connection, error, loading, resyncRequired, lastSync, action, refresh, selectTask } = useAppStore()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [risk, setRisk] = useState('all')
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  const [decisionError, setDecisionError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const pending = useRef(false)
  const latestSync = useRef(lastSync)
  useEffect(() => { latestSync.current = lastSync }, [lastSync])
  const [uncertain, setUncertain] = useState<{ id: string; sync: string | null } | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [checkedAt, setCheckedAt] = useState(() => Date.now())
  useEffect(() => {
    const next = approvals.filter(a => a.status === 'pending').map(a => Date.parse(a.expiresAt)).filter(time => Number.isFinite(time) && time > checkedAt).sort((a,b)=>a-b)[0]
    if (next === undefined) return
    const timer = window.setTimeout(() => setCheckedAt(Date.now()), Math.min(Math.max(0, next - Date.now()) + 1, 2147483647))
    return () => window.clearTimeout(timer)
  }, [approvals, checkedAt])
  const stale = Boolean(error || resyncRequired || connection !== 'connected')
  const expired = (a: Approval, now = checkedAt) => a.status === 'expired' || !Number.isFinite(Date.parse(a.expiresAt)) || Date.parse(a.expiresAt) <= now
  const unavailable = (a: Approval) => loading || !system || stale || system.emergencyStop || Boolean(busy) || a.status !== 'pending' || expired(a) || (uncertain?.id === a.id && uncertain.sync === lastSync)
  const decide = async (a: Approval, decision: 'approve' | 'reject') => {
    if (pending.current || expired(a, Date.now()) || unavailable(a) || (decision === 'approve' && a.riskLevel === 'black')) return
    if (decision === 'approve' && a.riskLevel === 'red' && !window.confirm(`Approve this red-risk demonstration decision?\n${a.title}\n${a.exactActionPreview}\nThis does not authorize native workspace execution.`)) return
    pending.current = true
    setBusy(a.id); setMessage(''); setDecisionError('')
    try {
      await action(`/api/approvals/${encodeURIComponent(a.id)}/${decision}`, { decisionNote: notes[a.id]?.trim() || null })
      setMessage(`Approval ${decision === 'approve' ? 'approved' : 'rejected'}. Decision request acknowledged; inspect the refreshed record.`)
    } catch (caught) {
      if (caught instanceof ApiError) setDecisionError(`${caught.message} (${caught.code}). Refresh the record before another decision.`)
      else {
        setUncertain({ id: a.id, sync: latestSync.current })
        setDecisionError('Decision outcome could not be confirmed. Refresh state before retrying; the server may have recorded it. No automatic replay was sent.')
      }
    } finally { pending.current = false; setBusy(null) }
  }
  const visible = approvals.filter(a => (status === 'all' || a.status === status) && (risk === 'all' || a.riskLevel === risk) && [a.title,a.description,a.reason,a.actionType,a.id,a.taskId,a.requestedByAgentId,tasks.find(t=>t.id===a.taskId)?.title,agents.find(t=>t.id===a.requestedByAgentId)?.name].join(' ').toLowerCase().includes(query.trim().toLowerCase()))
  return <div className="approval-operations">
    <header className="page-title"><div><h1>Approval inbox</h1><p>Demonstration decision records. Workspace plan authorization is reviewed in Planning.</p><Link to="/runtime">Open Planning →</Link></div></header>
    {(stale || loading || !system || system.emergencyStop) && <p className="approval-notice" role="status">{loading ? 'Loading approval state.' : system?.emergencyStop ? 'Emergency stop is active. Approval actions are blocked.' : !system ? 'System state is unavailable. Refresh before deciding.' : 'Last-known approval data may be outdated. Refresh before deciding.'} {error}</p>}
    {message && <p className="approval-notice" role="status">{message}</p>}
    {decisionError && <p className="approval-notice approval-warning" role="alert">{decisionError}</p>}
    <div className="approval-toolbar"><label>Search approvals<input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Title, task, agent or identifier"/></label><label>Status<select aria-label="Status" value={status} onChange={e=>setStatus(e.target.value)}>{['all','pending','approved','rejected','expired','cancelled'].map(value=><option key={value} value={value}>{value === 'all' ? 'All' : value}</option>)}</select></label><label>Risk<select aria-label="Risk" value={risk} onChange={e=>setRisk(e.target.value)}>{['all','green','yellow','orange','red','black'].map(value=><option key={value} value={value}>{value === 'all' ? 'All' : value}</option>)}</select></label><button disabled={refreshing || Boolean(busy)} onClick={async()=>{setRefreshing(true);try{await refresh()}finally{setRefreshing(false)}}}>{refreshing?'Refreshing…':'Refresh state'}</button></div>
    <p className="approval-scope">{visible.length} of {approvals.length} records shown</p>
    {!visible.length && !loading && <p>{approvals.length ? 'No approvals match these filters.' : stale || !system ? 'Approval records have not been confirmed.' : 'No demonstration approval records are available.'}</p>}
    <div className="approval-record-list">{visible.map(a=>{
      const isExpired = expired(a)
      const blocked = unavailable(a) || Boolean(busy)
      return <article className="approval-record" key={a.id} aria-label={a.title}>
        <header><h2>{a.title}</h2><div className="approval-record-state"><span className={`approval-risk risk-${a.riskLevel}`}>{a.riskLevel} risk</span><span>{a.status}</span></div></header>
        <p className="approval-context">Agent: {agents.find(x=>x.id===a.requestedByAgentId)?.name ?? a.requestedByAgentId} <span>Task: {tasks.find(x=>x.id===a.taskId)?.title ?? a.taskId}</span></p>
        <div className="approval-record-layout"><div>
          <dl className="approval-record-details"><div><dt>Description</dt><dd>{a.description}</dd></div><div><dt>Reason</dt><dd>{a.reason}</dd></div><div className="approval-preview"><dt>Exact preview</dt><dd><pre>{a.exactActionPreview}</pre></dd></div><div><dt>Affected resources</dt><dd>{a.affectedResources.join(', ') || 'None reported'}</dd></div><div><dt>Expected result</dt><dd>{a.expectedOutcome}</dd></div><div><dt>Reversal</dt><dd>{a.reversalMethod}</dd></div><div><dt>Expires</dt><dd>{new Date(a.expiresAt).toLocaleString()}</dd></div></dl>
          {a.status === 'pending' && !isExpired && <label className="approval-note">Decision note (optional)<textarea aria-label={`Decision note for ${a.title}`} maxLength={500} value={notes[a.id] ?? ''} disabled={blocked} onChange={e=>setNotes({...notes,[a.id]:e.target.value})}/><small>{(notes[a.id] ?? '').length}/500</small></label>}
          {isExpired && <p className="approval-warning">Expired approvals cannot be processed.</p>}
          {a.riskLevel==='black' && <p className="approval-warning">Black-risk actions are prohibited and cannot be approved.</p>}
          {uncertain?.id===a.id && uncertain.sync===lastSync && <p className="approval-warning">Refresh state to reconcile this decision before retrying.</p>}
          <div className="approval-record-actions"><button className="approval-approve" disabled={blocked || a.riskLevel==='black'} onClick={()=>void decide(a,'approve')}>{busy===a.id?'Awaiting acknowledgement…':'Approve'}</button><button disabled={blocked} onClick={()=>void decide(a,'reject')}>Reject</button><button className="approval-open-task" disabled={!tasks.some(t=>t.id===a.taskId)} onClick={()=>selectTask(a.taskId)}>Open task</button></div>
        </div><aside><p>Approving records this demonstration decision. It does not authorize native workspace execution. Rejecting records a refusal.</p><details><summary>Technical details</summary><dl><dt>Record ID</dt><dd>{a.id}</dd><dt>Action type</dt><dd>{a.actionType}</dd><dt>Task ID</dt><dd>{a.taskId}</dd><dt>Agent ID</dt><dd>{a.requestedByAgentId}</dd><dt>Created</dt><dd>{new Date(a.createdAt).toLocaleString()}</dd></dl></details>{a.status!=='pending' && <details><summary>Reviewed decision</summary><dl><dt>Reviewed by</dt><dd>{a.reviewedBy ?? 'Not recorded'}</dd><dt>Reviewed at</dt><dd>{a.reviewedAt ? new Date(a.reviewedAt).toLocaleString() : 'Not recorded'}</dd><dt>Decision note</dt><dd>{a.decisionNote ?? 'Not recorded'}</dd></dl></details>}</aside></div>
      </article>
    })}</div>
  </div>
}
