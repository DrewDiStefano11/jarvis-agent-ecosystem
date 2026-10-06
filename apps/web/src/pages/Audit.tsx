import { useState } from 'react'
import { useAppStore } from '../state/AppStore'
import type { AuditEvent } from '../types/contracts'
import { ActivityEvidence } from '../components/ActivityEvidence'
import { redactEvidenceText } from '../components/activity-evidence'
import '../styles/activity.css'

const safe = (value: string) => redactEvidenceText(value).text
function ActivityRow({ event, actorName, taskName, onOpenTask }: { event: AuditEvent; actorName: string; taskName: string; onOpenTask?: () => void }) {
  const [open, setOpen] = useState(false)
  return <li><details onToggle={e=>setOpen(e.currentTarget.open)}><summary>
    <time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleString()}</time><span className="activity-sequence">#{event.sequenceNumber}</span>
    <span className="activity-event-type">{safe(event.eventType)}</span><strong>{safe(event.summary)}</strong>
    <span className="activity-context">{actorName} · {taskName}{event.payload.simulated===true && <small>Demonstration</small>}</span>
  </summary>{open && <ActivityEvidence event={event} actorName={actorName} taskName={taskName} onOpenTask={onOpenTask}/>}</details></li>
}

export function Audit() {
  const { auditEvents, agents, tasks, connection, error, loading, resyncRequired, refresh, selectTask } = useAppStore()
  const [actor, setActor] = useState('all')
  const [category, setCategory] = useState('all')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(50)
  const [refreshing, setRefreshing] = useState(false)
  const stale = Boolean(error || resyncRequired || connection!=='connected')
  const actors = Array.from(new Set([...auditEvents.map(e=>e.actorAgentId), ...(actor==='all'?[]:[actor==='system'?null:actor.slice(6)])])).sort((a,b)=>(a??'').localeCompare(b??''))
  const categories = Array.from(new Set([...auditEvents.map(e=>e.eventType.split('.')[0]!), ...(category==='all'?[]:[category])])).sort()
  const actorName = (id: string | null) => id===null ? 'System' : safe(agents.find(a=>a.id===id)?.name ?? id)
  const taskName = (id: string | null) => id===null ? 'No task' : safe(tasks.find(t=>t.id===id)?.title ?? id)
  const filtered = auditEvents.filter(e=>(actor==='all' || (actor==='system'?e.actorAgentId===null:`actor:${e.actorAgentId}`===actor)) && (category==='all' || e.eventType.split('.')[0]===category) && [safe(e.summary),safe(e.eventType),safe(e.id),safe(e.taskId ?? ''),safe(e.actorAgentId ?? ''),actorName(e.actorAgentId),taskName(e.taskId)].join(' ').toLowerCase().includes(query.trim().toLowerCase())).slice().sort((a,b)=>b.sequenceNumber-a.sequenceNumber || b.timestamp.localeCompare(a.timestamp) || a.id.localeCompare(b.id))
  const shown = filtered.slice(0,limit)
  return <div className="activity-page"><header className="page-title"><div><h1>Activity</h1><p>Stored audit records may include seeded and demonstration activity. This collection is not complete runtime history.</p></div></header>
    {(stale || loading) && <p className="activity-notice" role="status">{loading?'Loading activity.':'Last-known activity may be outdated. Refresh to reconcile.'} {error ? safe(error) : ''}</p>}
    <div className="activity-toolbar"><label>Search activity<input type="search" value={query} placeholder="Summary, event, task or actor" onChange={e=>{setQuery(e.target.value);setLimit(50)}}/></label>
      <label>Actor<select aria-label="Actor" value={actor} onChange={e=>{setActor(e.target.value);setLimit(50)}}><option value="all">All</option>{actors.map(id=><option key={id===null?'system':`actor:${id}`}  value={id===null?'system':`actor:${id}`} >{actorName(id)}</option>)}</select></label>
      <label>Category<select aria-label="Category" value={category} onChange={e=>{setCategory(e.target.value);setLimit(50)}}><option value="all">All</option>{categories.map(value=><option key={value} value={value}>{safe(value)}</option>)}</select></label>
      <button disabled={refreshing} onClick={async()=>{setRefreshing(true);try{await refresh()}finally{setRefreshing(false)}}}>{refreshing?'Refreshing…':'Refresh state'}</button></div>
    <p className="activity-scope">Showing {shown.length} of {filtered.length} matching loaded records. Newest sequence first.</p>
    {!shown.length && !loading && <p>{auditEvents.length?'No activity matches these filters.':stale?'Activity records have not been confirmed.':'No stored audit records are available.'}</p>}
    <ol className="activity-list">{shown.map(event=><ActivityRow key={event.id} event={event} actorName={actorName(event.actorAgentId)} taskName={taskName(event.taskId)} onOpenTask={event.taskId && tasks.some(t=>t.id===event.taskId)?()=>selectTask(event.taskId):undefined}/>)}</ol>
    {shown.length<filtered.length && limit<200 && <button className="activity-load" onClick={()=>setLimit(Math.min(200,limit+50))}>Load 50 more</button>}
    {filtered.length>200 && limit>=200 && <p className="activity-notice">Display limited to 200 records. Refine the filters to inspect other loaded records.</p>}
  </div>
}
