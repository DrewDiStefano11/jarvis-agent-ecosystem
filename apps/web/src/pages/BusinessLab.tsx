import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { TaskCreateForm } from '../components/TaskCreateForm'
import { Empty, Progress, Status } from '../components/Status'
import { useAppStore } from '../state/AppStore'
import {
  LANE_COPY,
  OBJECTIVE_LANES,
  businessObjectives,
  filterObjectives,
  formatTimestamp,
  laneCounts,
  objectiveLane,
  participantLabel,
  recordedStatuses,
  sortObjectives,
  type ObjectiveLane,
  type ObjectiveSort,
} from './business-lab/objectives'
import '../styles/business-lab.css'

export function BusinessLab() {
  const { tasks, artifacts, agents, runtime, selectTask, loading, error, connection, lastSync, refresh, resyncRequired } = useAppStore()
  const [creating, setCreating] = useState(false)
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [lane, setLane] = useState<ObjectiveLane | ''>('')
  const [sort, setSort] = useState<ObjectiveSort>('updated-desc')
  const createHeading = useRef<HTMLHeadingElement>(null)
  const searchId = useId()
  const { loadIdentities, identityError, identityLoading } = runtime
  useEffect(() => { void loadIdentities().catch(() => undefined) }, [loadIdentities])
  useEffect(() => { if (creating) createHeading.current?.focus() }, [creating])

  const objectives = useMemo(() => businessObjectives(tasks), [tasks])
  const counts = useMemo(() => laneCounts(tasks), [tasks])
  const statuses = useMemo(() => recordedStatuses(tasks), [tasks])
  const visible = useMemo(
    () => sortObjectives(filterObjectives(tasks, { query, status, lane }), sort),
    [tasks, query, status, lane, sort],
  )
  const lanes = lane ? OBJECTIVE_LANES.filter(item => item === lane) : OBJECTIVE_LANES
  const filtersActive = Boolean(query.trim() || status || lane)
  const artifactCount = (taskId: string) => artifacts.filter(item => item.taskId === taskId).length

  return <div className="lab">
    <header className="page-title">
      <div>
        <p className="eyebrow">Objectives and reports</p>
        <h1>Business Lab</h1>
        <p>Find, create, and inspect objectives for the local workforce. Saving an objective only queues it.</p>
      </div>
      <button className="primary" type="button" aria-expanded={creating} aria-controls="lab-create" onClick={() => setCreating(value => !value)}>
        {creating ? 'Hide objective form' : 'New objective'}
      </button>
    </header>

    <section className="panel lab-purpose" aria-labelledby="lab-purpose-title">
      <h2 id="lab-purpose-title">What this workspace does</h2>
      <p>Supply the facts and the report you want. Open the objective workspace to prepare a local planner and queue a workspace action plan. Inspect proposed file contents, then explicitly authorize a configured workspace.</p>
      <p>Reading files returns observations. Automatic research and adaptive follow-up from those reads are not implemented. Creating an objective does not start planning or execution.</p>
      <p><Link to="/agents">Manage workforce identities</Link> · <Link to="/runtime?mode=workspace">Open the planning workspace</Link></p>
      <p className="lab-meta"><span>Sync <Status value={connection} /></span>{lastSync ? <span>Last synchronized {formatTimestamp(lastSync)}</span> : <span>Not synchronized yet</span>}</p>
    </section>

    {creating && <section className="panel lab-create" id="lab-create" aria-labelledby="lab-create-title">
      <h2 id="lab-create-title" ref={createHeading} tabIndex={-1}>Create a queued objective</h2>
      <p>Title and description are stored on the task. Priority is the recorded task priority, not a budget or profitability score. After a confirmed save, open the workspace and prepare a plan yourself.</p>
      <TaskCreateForm projectId="business-lab" onCreated={(task, warning) => {
        runtime.setTaskId(task.id)
        setCreating(false)
        setMessage(`Objective saved and still queued. Open its workspace when you want to prepare a plan.${warning ? ` ${warning}` : ''}`)
        setLane('')
        setStatus('')
        setQuery('')
      }} />
    </section>}
    {message && <p className="callout success" role="status">{message}</p>}

    {loading && !objectives.length && <p role="status">Loading objectives…</p>}
    {error && <p className="callout danger" role="alert">{error}{objectives.length ? ' Showing the last synchronized objectives, which may be stale.' : ' No objectives are available until synchronization succeeds.'}</p>}
    {resyncRequired && !error && <p className="callout warning" role="status">Control outcome requires reconciliation. Refresh before relying on this list.</p>}
    {identityError && <p className="callout danger" role="alert">Workforce identities could not be loaded: {identityError}. Assigned names may show unresolved ids.</p>}
    {identityLoading && <p role="status">Loading workforce identities…</p>}

    <div className="lab-summary" role="group" aria-label="Objective stages">
      {OBJECTIVE_LANES.map(item => <button key={item} type="button" className="lab-chip" aria-pressed={lane === item} onClick={() => setLane(current => current === item ? '' : item)}>
        {LANE_COPY[item].title}<small>{counts[item]}</small>
      </button>)}
    </div>

    <div className="filters lab-toolbar">
      <label htmlFor={searchId}>Search objectives
        <input id={searchId} value={query} onChange={event => setQuery(event.target.value)} placeholder="Title or description" autoComplete="off" />
      </label>
      <label>Recorded status
        <select aria-label="Recorded status" value={status} onChange={event => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {statuses.map(item => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}
        </select>
      </label>
      <label>Sort within stage
        <select aria-label="Sort objectives" value={sort} onChange={event => setSort(event.target.value as ObjectiveSort)}>
          <option value="updated-desc">Recently updated</option>
          <option value="updated-asc">Oldest update</option>
          <option value="title">Title</option>
        </select>
      </label>
    </div>
    <p className="lab-meta" aria-live="polite">
      <span>Showing {visible.length} of {objectives.length} objectives{artifacts.length ? ` · ${artifacts.length} artifacts in the current snapshot` : ''}</span>
      <span className="lab-actions">
        {filtersActive && <button type="button" className="secondary lab-clear" onClick={() => { setQuery(''); setStatus(''); setLane('') }}>Clear filters</button>}
        <button type="button" className="secondary" onClick={() => void refresh()}>{loading ? 'Refreshing…' : 'Refresh objectives'}</button>
      </span>
    </p>

    {!loading && !error && !objectives.length && <Empty>Create your first objective. It stays queued until you explicitly prepare and launch its plan.</Empty>}
    {!loading && objectives.length > 0 && !visible.length && <div className="empty lab-empty">No objectives match this search or filter. Clear the filters to see every Business Lab objective.</div>}

    <div className="lab-lanes">
      {lanes.map(item => {
        const rows = visible.filter(task => objectiveLane(task.status) === item)
        if (!rows.length) return null
        return <section key={item} className={`panel lab-lane lab-lane-${item}`} aria-labelledby={`lab-lane-${item}`}>
          <header>
            <h2 id={`lab-lane-${item}`}>{LANE_COPY[item].title} <span className="lab-stage">{rows.length}</span></h2>
            <p>{LANE_COPY[item].detail}</p>
          </header>
          <div className="lab-cards">
            {rows.map(task => {
              const participants = task.assignedAgentIds.map(id => participantLabel(id, runtime.identities, agents))
              const recordedArtifacts = Math.max(task.artifactIds.length, artifactCount(task.id))
              return <article className="panel lab-card" key={task.id} aria-labelledby={`objective-${task.id}`}>
                <div className="task-top">
                  <h3 id={`objective-${task.id}`}>{task.title}</h3>
                  <span><span className="lab-stage">{LANE_COPY[item].title}</span> <Status value={task.status} /></span>
                </div>
                <p>{task.description || 'No description recorded.'}</p>
                {task.statusMessage && <p>{task.statusMessage}</p>}
                <Progress value={task.progress} label="Recorded progress" />
                <p className="lab-facts">
                  <span>Priority {task.priority}</span>
                  <span>Updated {formatTimestamp(task.updatedAt)}</span>
                  <span>{task.result ? 'Result recorded' : 'No result recorded'}</span>
                  <span>{recordedArtifacts === 1 ? '1 recorded artifact' : `${recordedArtifacts} recorded artifacts`}</span>
                </p>
                {task.result && <p className="lab-result">{task.result}</p>}
                {task.error && <p role="alert">{task.error.code}: {task.error.message}</p>}
                <p>Participating agents: {participants.length ? participants.join(', ') : 'No agent assigned yet'}</p>
                <p className="lab-actions">
                  <Link to="/runtime?mode=workspace" onClick={() => runtime.setTaskId(task.id)}>Open objective workspace</Link>
                  <button type="button" className="secondary" onClick={() => selectTask(task.id)}>Inspect task history</button>
                </p>
                <p className="muted">Opening the workspace selects this objective. It does not queue a plan or approve execution.</p>
                {task.correctionOfTaskId && <p>Corrected follow-up · <button type="button" className="secondary" onClick={() => selectTask(task.correctionOfTaskId!)}>Inspect original objective</button></p>}
              </article>
            })}
          </div>
        </section>
      })}
    </div>
  </div>
}
