import { Link } from 'react-router-dom'
import { NavIcon } from '../components/NavIcon'
import { Status } from '../components/Status'
import { useAppStore } from '../state/AppStore'

const terminalRuns = new Set(['succeeded', 'cancelled', 'failed', 'timed_out', 'abandoned'])
export function Dashboard() {
  const { system, tasks, agents, approvals, auditEvents, runtime, selectTask, error } = useAppStore()
  const worker = system?.autonomousWorker
  const pending = approvals.filter(approval => approval.status === 'pending')
  const roots = tasks.filter(task => !task.parentTaskId).sort((a, b) => {
    const finished = (status: string) => ['completed', 'cancelled'].includes(status) ? 1 : 0
    return finished(a.status) - finished(b.status) || b.updatedAt.localeCompare(a.updatedAt)
  }).slice(0, 5)
  const runs = runtime.runs.filter(run => !terminalRuns.has(run.state))
  const attention: { title: string; detail: string; path: string }[] = []
  if (system?.emergencyStop) attention.push({ title: 'Emergency stop is active', detail: 'Review interrupted work before resuming the system.', path: '/system' })
  if (system && (!system.databaseHealthy || !system.schemaCurrent)) attention.push({ title: 'Database needs attention', detail: 'Inspect connectivity and migration state.', path: '/system' })
  if (system?.outboxExhaustedCount) attention.push({ title: `${system.outboxExhaustedCount} event deliveries exhausted`, detail: 'Durable events need operator reconciliation.', path: '/system' })
  if (system?.staleWorkerCount || system?.expiredLeaseCount) attention.push({ title: 'Worker or lease recovery needed', detail: `${system.staleWorkerCount} stale workers · ${system.expiredLeaseCount} expired leases`, path: '/system' })
  if (worker?.enabled && worker.status !== 'healthy' && worker.status !== 'ready') attention.push({ title: `Local execution: ${worker.status}`, detail: worker.reasonCode?.replaceAll('_', ' ') ?? 'Inspect worker configuration and provider readiness.', path: '/runtime' })
  if (system?.recoveryRequired) attention.push({ title: 'Demonstration recovery required', detail: 'A simulator workflow remains at its persisted checkpoint.', path: '/system' })
  if (system?.status === 'degraded' && attention.length === 0) attention.push({ title: 'System is degraded', detail: 'Inspect existing runtime and persistence diagnostics.', path: '/system' })
  const health = [
    ['API', system?.status ?? 'unavailable'],
    ['Database', !system ? 'unavailable' : !system.databaseHealthy ? 'unavailable' : system.schemaCurrent ? 'current' : 'stale'],
    ['Worker', worker ? worker.enabled ? worker.status : 'disabled' : 'unavailable'],
    ['Local provider', worker ? worker.providerReady ? 'ready' : 'not ready' : 'unavailable'],
    ['Event delivery', !system ? 'unavailable' : system.outboxExhaustedCount ? 'exhausted' : system.outboxPendingCount ? `${system.outboxPendingCount} pending` : 'clear'],
    ['Emergency stop', !system ? 'unavailable' : system.emergencyStop ? 'active' : 'clear'],
  ]
  return <div className="mission-overview">
    <header className="page-title"><div><h1>Mission Control</h1><p>Runtime health, operator attention, and task requests.</p></div><Link className="primary" to="/tasks?create=1">+ New task</Link></header>
    <section className={`mc-panel attention-panel${attention.length ? ' attention-needed' : ''}`} aria-labelledby="attention-heading"><NavIcon name="attention"/><div><h2 id="attention-heading">Needs attention</h2>{!system ? <p>System health could not be confirmed. Inspect synchronization before acting.</p> : !attention.length ? <p>{error ? 'Last-known snapshot reported no system blockers. Refresh to confirm.' : 'No system blockers reported'}</p> : <ul className="attention-list">{attention.map(item => <li key={item.title}><Link to={item.path}>{item.title}<span aria-hidden="true"> →</span></Link><small>{item.detail}</small></li>)}</ul>}{system && error && <p className="mc-note">Last-known blockers shown. System health could not be refreshed.</p>}<p className="mc-note">Task and approval records may include demonstration work. {pending.length > 0 && <Link to="/approvals">Review {pending.length} pending approval records →</Link>}</p></div></section>
    <div className="mission-grid">
      <section className="mc-panel runtime-panel" aria-labelledby="runtime-heading"><div className="mc-heading"><h2 id="runtime-heading">Runtime work</h2><Link to="/runtime">Open planning ↗</Link></div>
        {!runtime.actorId ? <div className="runtime-empty"><NavIcon name="activity"/><strong>Select an identity to inspect runtime work</strong><p>Runtime visibility is scoped to the identity selected in Planning.</p><Link className="primary" to="/runtime">Choose identity</Link></div> : <><p className="mc-note">Selected identity: {runtime.identities.find(identity => identity.id === runtime.actorId)?.display_name ?? runtime.actorId}. {runtime.taskId ? 'Filtered to the selected task.' : 'Up to 50 visible runs.'}</p>{runtime.error ? <p role="alert">{runtime.error}</p> : runtime.loading ? <p role="status">Synchronizing authorized runs…</p> : runs.length ? <ul className="mc-records">{runs.slice(0, 6).map(run => <li key={run.specification.run_id}><Link to="/runtime" onClick={() => runtime.setTaskId(run.specification.task_id)}><NavIcon name="planning"/><span><strong>{tasks.find(task => task.id === run.specification.task_id)?.title ?? run.specification.task_id}</strong><small>{run.status_detail ?? run.specification.requested_operation} · Attempt {run.attempt_count}</small></span><Status value={run.state}/></Link></li>)}</ul> : <div className="runtime-empty"><strong>No active runs in this view</strong><p>This reflects the current identity, task filter, and fetched page. It is not a system-wide total.</p><Link to="/runtime">Inspect run history →</Link></div>}{runtime.nextOffset !== null && <p className="mc-note">More runs exist beyond this page. <Link to="/runtime">Inspect runtime history</Link>.</p>}</>}
      </section>
      <section className="mc-panel health-panel" aria-labelledby="health-heading"><h2 id="health-heading">System health</h2><dl className="mc-health">{health.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><Status value={value!}/></dd></div>)}</dl><Link className="secondary mc-wide" to="/system"><NavIcon name="system"/>Inspect system</Link></section>
      <section className="mc-panel requests-panel" aria-labelledby="requests-heading"><div className="mc-heading"><h2 id="requests-heading">Task requests</h2><Link to="/tasks">View all →</Link></div><p className="mc-note">Durable task records; execution provenance is not available for every request.</p><ul className="mc-records">{roots.map(task => <li key={task.id}><button onClick={() => selectTask(task.id)} aria-label={`Inspect ${task.title}`}><NavIcon name="tasks"/><span><strong>{task.title}</strong><small>{task.statusMessage}{task.blockedBy.length > 0 ? ` · ${task.blockedBy.length} blockers` : ''}</small></span><Status value={task.status}/><span aria-hidden="true">›</span></button></li>)}</ul>{!roots.length && <p className="mc-note">No task requests in the current snapshot.</p>}</section>
      <section className="mc-panel activity-panel" aria-labelledby="activity-heading"><div className="mc-heading"><h2 id="activity-heading">Recent activity</h2><Link to="/audit">View all →</Link></div><p className="mc-note">May include simulator events.</p><ol className="mc-activity">{auditEvents.slice(-4).reverse().map(event => <li key={event.id}><NavIcon name="activity"/><div><strong>{event.summary}</strong><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleString()} · #{event.sequenceNumber}</time></div></li>)}</ol>{!auditEvents.length && <p className="mc-note">No activity in the current snapshot.</p>}</section>
    </div>
    <details className="mc-panel demo-disclosure"><summary>Demonstration state <span>Simulator {system?.simulator.state ?? 'unavailable'} · {agents.length} demonstration agents</span></summary><p className="mc-note">These resource values and seeded agent roles describe deterministic simulation, not real model utilization.</p><div className="resource-grid">{system?.resources.map(resource => <div key={resource.name}><span>{resource.name}</span><strong>{resource.value}</strong><small>{resource.label}</small></div>)}</div><Link to="/agents">Inspect demonstration agents</Link></details>
  </div>
}
