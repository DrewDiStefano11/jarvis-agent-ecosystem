import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import '../styles/system-health.css'

function HealthRow({ label, value, tone = 'muted' }: { label: string; value: ReactNode; tone?: string }) {
  return <div className="system-health-row"><dt>{label}</dt><dd className={`system-value tone-${tone}`}>{value}</dd></div>
}

export function System() {
  const { system, connection, lastSync, error, resyncRequired, action, refresh } = useAppStore()
  const [message, setMessage] = useState('')
  const [controlError, setControlError] = useState('')
  const [busy, setBusy] = useState(false)
  const controlPending = useRef(false)
  const [refreshing, setRefreshing] = useState(false)
  const worker = system?.autonomousWorker
  const stale = Boolean(error || resyncRequired || connection !== 'connected')
  const demoDisabled = busy || !system || system.emergencyStop || stale
  const unknown = 'Unavailable'
  const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : 'Not recorded'
  const run = async (path: string, label: string, body?: unknown, confirmation?: string) => {
    if (controlPending.current || (path !== '/api/system/emergency-stop' && stale) || (confirmation && !window.confirm(confirmation))) return
    controlPending.current = true
    setBusy(true); setMessage(''); setControlError('')
    try { await action(path, body); setMessage(`${label} request acknowledged. Inspect the current state before further action.`) }
    catch (caught) { setControlError(`Request outcome could not be confirmed. Refresh state before retrying. ${caught instanceof Error ? caught.message : 'Control unavailable.'}`) }
    finally { controlPending.current = false; setBusy(false) }
  }
  const technical: [string, ReactNode][] = [
    ['Backend', system?.status ?? unknown], ['Storage', system?.storageBackend ?? unknown],
    ['Migration', system ? `${system.databaseRevision} (${system.schemaCurrent ? 'current' : 'stale'})` : unknown],
    ['Pending outbox', system?.outboxPendingCount ?? unknown], ['Exhausted outbox', system?.outboxExhaustedCount ?? unknown],
    ['Active workers', system?.activeWorkerCount ?? unknown], ['Active task leases', system?.activeLeaseCount ?? unknown],
    ['Expired task leases', system?.expiredLeaseCount ?? unknown], ['Stale workers', system?.staleWorkerCount ?? unknown],
    ['Worker identity', worker ? worker.workerActorId ?? 'Not configured' : unknown], ['Worker heartbeat', date(worker?.lastWorkerHeartbeat)],
    ['Last successful execution', date(worker?.lastSuccessfulExecutionAt)], ['Worker reason', worker?.reasonCode ?? 'None reported'],
    ['Context assembler', system?.contextAssembler?.state ?? unknown], ['Context assemblies', system?.contextAssembler?.totalAssemblies ?? unknown],
    ['Context review', system?.contextAssembler?.reviewRequiredAssemblies ?? unknown], ['Context redactions', system?.contextAssembler?.redactions ?? unknown],
    ['API schema', system?.apiSchemaVersion ?? unknown], ['Event session', system?.eventSessionId ?? unknown],
    ['Last startup', date(system?.lastStartupAt)], ['Last clean shutdown', date(system?.lastCleanShutdown)],
    ['Environment', system?.environment ?? unknown], ['PWA', window.matchMedia('(display-mode: standalone)').matches ? 'Installed' : 'Browser mode'],
  ]
  return <div className="system-health-page">
    <header className="page-title"><div><h1>System</h1><p>Runtime health, synchronization, and operator controls.</p></div></header>
    <section className="system-attention" aria-label="System attention">
      {!system && <p role="alert">System health is unavailable. Refresh state to inspect current conditions.</p>}
      {stale && <p>Last-known data may be outdated. {error ?? (resyncRequired ? 'An event sequence gap needs reconciliation.' : 'The event stream is disconnected.')}</p>}
      {system?.emergencyStop && <p role="alert">Emergency stop is active. Review interrupted work before resuming.</p>}
      {system?.recoveryRequired && <p role="alert">An interrupted demonstration workflow is preserved at its last checkpoint. Review the state, then use Resume demo to continue safely.</p>}
      {Boolean(system?.outboxExhaustedCount) && <p role="alert">Outbox delivery is exhausted for {system?.outboxExhaustedCount} durable event{system?.outboxExhaustedCount === 1 ? '' : 's'}. The records remain stored for inspection.</p>}
      {Boolean(system?.expiredLeaseCount) && <p role="alert">{system?.expiredLeaseCount} expired task lease{system?.expiredLeaseCount === 1 ? ' is' : 's are'} awaiting recovery.</p>}
      {Boolean(system?.staleWorkerCount) && <p role="alert">{system?.staleWorkerCount} active worker heartbeat{system?.staleWorkerCount === 1 ? ' is' : 's are'} stale.</p>}
      {system && (!system.databaseHealthy || !system.schemaCurrent) && <p role="alert">Database connectivity or schema needs attention. Inspect the technical details and existing runtime diagnostics.</p>}
      {worker?.enabled && worker.status === 'degraded' && <p role="alert">Local execution is degraded. {worker.reasonCode?.replaceAll('_', ' ') ?? 'Inspect worker and provider readiness in Planning.'}</p>}
    </section>
    {message && <p className="system-command-message" role="status">{message}</p>}
    {controlError && <p className="system-command-error" role="alert">{controlError}</p>}
    <div className="system-health-layout">
      <section className="system-health-panel system-health-snapshot" aria-labelledby="system-snapshot-title"><h2 id="system-snapshot-title">Health snapshot</h2><p>{system ? `${stale ? 'Last-known' : 'Reported'} backend status: ${system.status}` : 'Current conditions have not been confirmed.'}</p><dl>
        <HealthRow label="Database" value={system ? system.databaseHealthy ? 'Reachable' : 'Unavailable' : unknown} tone={system ? system.databaseHealthy ? 'good' : 'danger' : 'muted'}/>
        <HealthRow label="Schema" value={system ? system.schemaCurrent ? 'Current' : 'Stale' : unknown} tone={system ? system.schemaCurrent ? 'good' : 'warning' : 'muted'}/>
        <HealthRow label="Workers" value={system ? `${system.activeWorkerCount} active / ${system.staleWorkerCount} stale` : unknown} tone={system?.staleWorkerCount ? 'warning' : 'muted'}/>
        <HealthRow label="Task leases" value={system ? `${system.activeLeaseCount} active / ${system.expiredLeaseCount} expired` : unknown} tone={system?.expiredLeaseCount ? 'warning' : 'muted'}/>
        <HealthRow label="Event delivery" value={system ? `${system.outboxPendingCount} pending / ${system.outboxExhaustedCount} exhausted` : unknown} tone={system?.outboxExhaustedCount ? 'danger' : 'muted'}/>
      </dl></section>
      <section className="system-health-panel system-health-controls" aria-labelledby="system-controls-title"><h2 id="system-controls-title">System controls</h2><p>Emergency stop blocks new execution and stops office movement. Review active work before resuming.</p><button className="system-stop" disabled={!system || busy || (system.emergencyStop && stale)} onClick={() => void run(system?.emergencyStop ? '/api/system/resume' : '/api/system/emergency-stop', system?.emergencyStop ? 'Resume system' : 'Emergency stop', undefined, system?.emergencyStop ? 'Resume the system? Review interrupted work first. Existing authorization and recovery rules still apply.' : 'Stop the system? New execution and office movement will stop. Inspect active work before resuming.')}>{busy ? 'Awaiting acknowledgement…' : system?.emergencyStop ? 'Resume system' : 'Emergency stop'}</button><small>Confirmation required. Existing authorization still applies. Refresh stale state before resuming.</small></section>
      <section className="system-health-panel system-local-execution" aria-labelledby="system-local-title"><h2 id="system-local-title">Local execution</h2><p>Explicitly queued local planning and its configured provider.</p><dl>
        <HealthRow label="Worker" value={worker?.status ?? unknown} tone={worker?.enabled && worker.status === 'degraded' ? 'warning' : 'muted'}/>
        <HealthRow label="Provider" value={worker ? worker.providerReady ? 'Ready' : 'Not ready' : unknown} tone={worker?.providerReady ? 'good' : 'muted'}/>
        <HealthRow label="Mode" value={worker?.modelExecutionMode?.replaceAll('_', ' ') ?? unknown}/>
      </dl><Link to="/runtime">Open Planning →</Link></section>
      <section className="system-health-panel system-sync" aria-labelledby="system-sync-title"><h2 id="system-sync-title">Synchronization</h2><p>Shared event stream and HTTP refresh.</p><dl><HealthRow label="Event stream" value={connection} tone={connection === 'connected' ? 'good' : 'warning'}/><HealthRow label="Last synchronized" value={lastSync ? date(lastSync) : 'Never'}/></dl><button disabled={refreshing} onClick={async () => { setRefreshing(true); try { await refresh() } finally { setRefreshing(false) } }}>{refreshing ? 'Refreshing…' : 'Refresh state'}</button></section>
    </div>
    <details className="system-health-panel system-technical"><summary>Technical details</summary><p>Reported contract, persistence and worker evidence.</p><dl className="details-grid">{technical.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></details>
    <details className="system-health-panel system-demonstration"><summary>Demonstration controls</summary><p>Deterministic simulator scenarios. System emergency controls and native planning are above.</p>{system?.emergencyStop && <p>Demo controls are unavailable during emergency stop. Review and resume through System controls first.</p>}{stale && <p>Refresh state before using demonstration controls.</p>}<dl className="details-grid"><div><dt>Simulator</dt><dd>{system?.simulator.state ?? unknown}</dd></div><div><dt>Seed data</dt><dd>{system?.seedDataVersion ?? unknown}</dd></div><div><dt>Demonstration recovery</dt><dd>{system ? system.recoveryRequired ? 'Required' : 'Clear' : unknown}</dd></div><div><dt>Demonstration workflow</dt><dd>{system?.activeWorkflowRunId ?? 'None reported'}</dd></div><div><dt>Last demonstration checkpoint</dt><dd>{system?.lastCheckpointId ?? 'None reported'}</dd></div></dl><div className="system-demo-actions">
      <button disabled={demoDisabled} onClick={() => void run('/api/simulator/start', 'Start demo')}>Start demo</button><button disabled={demoDisabled} onClick={() => void run('/api/simulator/pause', 'Pause demo')}>Pause demo</button><button disabled={demoDisabled} onClick={() => void run('/api/simulator/resume', 'Resume demo')}>Resume demo</button><button disabled={demoDisabled} onClick={() => void run('/api/simulator/reset', 'Reset demo', undefined, 'Reset demonstration state? This clears the emergency-stop flag. Review existing tasks before resetting.')}>Reset demo</button><button disabled={demoDisabled} onClick={() => void run('/api/simulator/failure', 'Trigger Scout failure', { scenario: 'scout_research_failure' })}>Trigger Scout failure</button><button disabled={demoDisabled} onClick={() => void run('/api/simulator/approval', 'Trigger approval')}>Trigger approval</button>
    </div></details>
  </div>
}
