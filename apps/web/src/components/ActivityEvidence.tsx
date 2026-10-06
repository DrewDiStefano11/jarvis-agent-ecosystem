import { useMemo, useState } from 'react'
import type { AuditEvent } from '../types/contracts'
import { displayedEvidence, EVIDENCE_LIMIT, redactEvidenceText } from './activity-evidence'

const safe = (value: string | null) => value === null ? 'Not recorded' : redactEvidenceText(value).text
export function ActivityEvidence({ event, taskName, actorName, onOpenTask }: { event: AuditEvent; taskName: string; actorName: string; onOpenTask?: () => void }) {
  const evidence = useMemo(() => displayedEvidence(event.payload), [event.payload])
  const [copyState, setCopyState] = useState('')
  const [copying, setCopying] = useState(false)
  const metadata = [
    ['Event time', new Date(event.timestamp).toLocaleString()], ['Event type', safe(event.eventType)],
    ['Actor', actorName], ['Transition', `${safe(event.previousState)} → ${safe(event.newState)}`],
    ['Related task', taskName], ['Task ID', safe(event.taskId)], ['Correlation ID', safe(event.correlationId)],
    ['Record ID', safe(event.id)], ['Provenance', event.payload.simulated === true ? 'Demonstration (reported by payload)' : 'Provenance not supplied by this record'],
  ]
  return <div className="activity-evidence">
    <dl>{metadata.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <div className="activity-evidence-heading"><h2>Event payload</h2><button disabled={copying} onClick={async()=>{
      setCopying(true);setCopyState('')
      try { await navigator.clipboard.writeText(evidence.text);setCopyState('Displayed evidence copied.') }
      catch { setCopyState('Clipboard unavailable. Select the displayed evidence to copy it manually.') }
      finally { setCopying(false) }
    }}>{copying?'Copying…':'Copy displayed evidence'}</button></div>
    <pre tabIndex={0} aria-label="Displayed event evidence">{evidence.text}</pre>
    <p className="activity-evidence-limits">{evidence.redacted ? 'Sensitive fields or patterns redacted.' : 'No recognized sensitive fields or patterns detected.'} Display bounded to {EVIDENCE_LIMIT.toLocaleString()} characters.{evidence.truncated && ' Content truncated; this is not the full payload.'}</p>
    {copyState && <p role="status">{copyState}</p>}
    {onOpenTask && <button onClick={onOpenTask}>Open task</button>}
  </div>
}
