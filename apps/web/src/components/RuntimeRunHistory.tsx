import { Fragment, useState } from 'react'
import { useAppStore } from '../state/AppStore'
import { RUNTIME_HISTORY_PAGE_LIMIT } from '../state/useRuntimeState'
import type { RuntimeRun } from '../types/runtime'
import './RuntimeRunHistory.css'

const states = ['created', 'queued', 'claimed', 'starting', 'running', 'pause_requested', 'paused', 'blocked', 'cancel_requested', 'cancelling', 'cancelled', 'succeeded', 'failed', 'timed_out', 'abandoned']
const terminal = new Set(['cancelled', 'succeeded', 'failed', 'timed_out', 'abandoned'])
const attention = new Set(['paused', 'blocked', 'failed', 'timed_out', 'abandoned'])
const label = (value: string) => value.replaceAll('_', ' ')
function bounded(value: string | null | undefined, limit = 180) { return value ? value.length > limit ? `${value.slice(0, limit)}… [truncated]` : value : 'Not supplied' }
function recorded(value: string | null | undefined) { if (!value) return 'Not supplied'; const date = new Date(value); return Number.isNaN(date.getTime()) ? 'Invalid recorded time' : date.toLocaleString() }
function Evidence({ run }: { run: RuntimeRun }) {
  const [open, setOpen] = useState(false)
  return <details className="run-history-evidence" onToggle={event => setOpen(event.currentTarget.open)}><summary>Technical details</summary>{open && <dl>
    <dt>Run ID</dt><dd>{run.specification.run_id}</dd><dt>Task ID</dt><dd>{run.specification.task_id}</dd>
    <dt>Target identity ID</dt><dd>{run.specification.agent_id}</dd><dt>Snapshot version</dt><dd>{run.version}</dd>
    <dt>Event sequence</dt><dd>{run.event_sequence_number ?? 'Not supplied'}</dd><dt>Active attempt</dt><dd>{run.active_attempt_id ?? 'None recorded'}</dd>
    <dt>Latest checkpoint</dt><dd>{run.latest_checkpoint_id ?? 'None recorded'}</dd><dt>Recovery status</dt><dd>{run.recovery_status ?? 'Not supplied'}</dd>
    <dt>Terminal outcome</dt><dd>{run.terminal_outcome ?? 'None recorded'}</dd><dt>Started</dt><dd>{recorded(run.started_at)}</dd><dt>Last heartbeat</dt><dd>{recorded(run.last_heartbeat_at)}</dd>
    <dt>Requested operation</dt><dd>{bounded(run.specification.requested_operation, 4000)}</dd>
    {run.failure && <><dt>Failure category</dt><dd>{run.failure.category}</dd><dt>Failure detail</dt><dd>{bounded(run.failure.detail, 4000)}</dd><dt>Failure recorded</dt><dd>{recorded(run.failure.timestamp)}</dd></>}
    {run.blocking_reason && <><dt>Blocking reason</dt><dd>{run.blocking_reason.code}: {bounded(run.blocking_reason.detail, 4000)}</dd><dt>Blocked recorded</dt><dd>{recorded(run.blocking_reason.timestamp)}</dd></>}
    {run.pause_reason && <><dt>Pause reason</dt><dd>{run.pause_reason.code}: {bounded(run.pause_reason.detail, 4000)}</dd><dt>Pause recorded</dt><dd>{recorded(run.pause_reason.timestamp)}</dd></>}
  </dl>}</details>
}
export function RuntimeRunHistory() {
  const { runtime, tasks, selectTask } = useAppStore()
  const [query, setQuery] = useState('')
  const [view, setView] = useState('All')
  const [state, setState] = useState('')
  const taskNames = new Map(tasks.map(task => [task.id, task.title]))
  const identityNames = new Map(runtime.identities.map(identity => [identity.id, identity.display_name]))
  const selectedTask = taskNames.get(runtime.taskId) ?? runtime.taskId
  const visible = runtime.runs.filter(run => {
    if (view === 'Active' && terminal.has(run.state)) return false
    if (view === 'Needs attention' && !attention.has(run.state) && run.recovery_status !== 'required' && run.recovery_status !== 'denied') return false
    if (view === 'Finished' && !terminal.has(run.state)) return false
    if (state && run.state !== state) return false
    return [run.specification.run_id, run.specification.task_id, taskNames.get(run.specification.task_id), run.specification.agent_id, identityNames.get(run.specification.agent_id), run.specification.requested_operation, run.state, run.status_detail, run.failure?.detail, run.blocking_reason?.detail, run.pause_reason?.detail].some(value => value?.toLowerCase().includes(query.trim().toLowerCase()))
  })
  return <section className="run-history" aria-labelledby="run-history-heading" aria-busy={runtime.loading}>
    <header><div><h2 id="run-history-heading">Runtime history</h2><p>Reading as {identityNames.get(runtime.actorId) ?? (runtime.actorId || 'no identity selected')} · {selectedTask || 'All tasks'}</p></div><button className="secondary" disabled={!runtime.actorId || runtime.loading} onClick={() => void runtime.refreshRuntime()}><svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 7v5h-5"/><path d="M20 12a8 8 0 1 0-2.5 5.8M20 7l-2.5-2.5"/></svg>Refresh runtime</button></header>
    <p className="run-history-scope">Authorized runs loaded for this identity. Loaded records are not a complete history total.</p>
    <div className="run-history-toolbar"><label>Search runs<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Task, operation, identity or run ID"/></label><fieldset><legend>View</legend>{['All', 'Active', 'Needs attention', 'Finished'].map(item => <button key={item} type="button" aria-pressed={view === item} onClick={() => setView(item)}>{item}</button>)}</fieldset><label>Status<select value={state} onChange={event => setState(event.target.value)}><option value="">All statuses</option>{states.map(item => <option key={item} value={item}>{label(item)}</option>)}</select></label></div>
    {runtime.error && <p role="alert">{runtime.error}</p>}
    {!runtime.actorId && <p>Select a local identity to read authorized history.</p>}
    {runtime.loading && <p role="status">{runtime.loadingMore ? 'Loading the next authorized page…' : 'Refreshing authorized history…'}</p>}
    {visible.length > 0 && <table><caption>{visible.length} matching of {runtime.runs.length} loaded runs</caption><thead><tr>{['Run', 'State', 'Target', 'Attempts', 'Recorded times', 'Inspect'].map(item => <th key={item} scope="col">{item}</th>)}</tr></thead><tbody>{visible.map(run => <Fragment key={run.specification.run_id}><tr>
      <td data-label="Run"><strong>{bounded(taskNames.get(run.specification.task_id) ?? run.specification.requested_operation)}</strong><span>{bounded(run.specification.requested_operation)}</span><code>{run.specification.run_id}</code></td>
      <td data-label="State"><span className={`run-history-state state-${run.state}`}>{label(run.state)}</span>{(run.status_detail || run.failure?.detail || run.blocking_reason?.detail || run.pause_reason?.detail) && <span>{bounded(run.status_detail || run.failure?.detail || run.blocking_reason?.detail || run.pause_reason?.detail)}</span>}</td>
      <td data-label="Target">{identityNames.get(run.specification.agent_id) ?? run.specification.agent_id}</td><td data-label="Attempts">{run.attempt_count}</td>
      <td data-label="Recorded times"><span>Created {recorded(run.created_at)}</span>{run.completed_at && <span>Completed {recorded(run.completed_at)}</span>}</td>
      <td data-label="Inspect"><button className="secondary" disabled={!taskNames.has(run.specification.task_id)} onClick={() => selectTask(run.specification.task_id)}>Open task</button>{!taskNames.has(run.specification.task_id) && <span>Task not loaded</span>}</td>
    </tr><tr className="run-history-detail-row"><td colSpan={6}><Evidence run={run}/></td></tr></Fragment>)}</tbody></table>}
    {runtime.actorId && !runtime.loading && !runtime.error && !visible.length && <p>{runtime.runs.length ? 'No loaded runs match these filters.' : runtime.nextOffset !== null ? 'No authorized runs in the loaded pages. More pages are available.' : 'No authorized runs in this selection.'}</p>}
    {runtime.actorId && <footer><span>{runtime.runs.length} loaded runs · {runtime.pagesLoaded} of {RUNTIME_HISTORY_PAGE_LIMIT} page requests used<br/>Filters search loaded records only · Oldest records first</span>{runtime.nextOffset !== null && runtime.pagesLoaded < RUNTIME_HISTORY_PAGE_LIMIT && <button className="secondary" disabled={runtime.loading} onClick={() => void runtime.loadMoreRuns()}>Load next page</button>}{runtime.nextOffset !== null && runtime.pagesLoaded >= RUNTIME_HISTORY_PAGE_LIMIT && <p>History is bounded to four pages (up to 200 runs). Select a task to narrow the scope, or refresh to start again.</p>}</footer>}
  </section>
}
