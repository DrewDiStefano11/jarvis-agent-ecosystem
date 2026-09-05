import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import { Status } from '../components/Status'
import { OfficeViewport } from '../office-reference/components/office/OfficeViewport'
import { LiveOfficeLayer } from '../components/LiveOfficeLayer'
import { loadFloor1CandidateOverlay, FLOOR1_CANDIDATE_LAYER_CONTROLS } from '../office-reference/office/floor1/candidateReview'
import type { OfficeLayer, OfficeOverlayDocument, ViewTransform } from '../office-reference/office/types'
import '../styles/office.css'

const EMPTY_FLOOR: OfficeOverlayDocument = { schemaVersion: 1, source: { width: 8192, height: 5460 }, production: false, entities: [], pathNodes: [] }
const ignorePointer = () => undefined

export function Office() {
  const { agents, tasks, selectAgent, selectTask, runtime, office, system, lastSync, connection } = useAppStore()
  const [floor, setFloor] = useState(EMPTY_FLOOR)
  const [candidate, setCandidate] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const [layers, setLayers] = useState<ReadonlySet<OfficeLayer>>(() => new Set(['rooms']))
  const [transform, setTransform] = useState<ViewTransform>({ x: 0, y: 0, scale: 1 })
  const [focus, setFocus] = useState(0)
  const [identityId, setIdentityId] = useState('')
  const [stationId, setStationId] = useState('')
  const [spriteId, setSpriteId] = useState('agent-sheet-01')
  const { loadIdentities } = runtime
  const { load: loadOffice } = office
  useEffect(() => { void loadOffice() }, [loadOffice])
  useEffect(() => { void loadIdentities().catch(caught => setError(caught instanceof Error ? caught.message : 'Cannot load workforce')) }, [loadIdentities, lastSync])
  useEffect(() => {
    if (!candidate) return
    let stale = false
    void loadFloor1CandidateOverlay().then(document => { if (!stale) setFloor(document) }).catch(caught => {
      if (!stale) setError(caught instanceof Error ? caught.message : 'Cannot load candidate geometry')
    })
    return () => { stale = true }
  }, [candidate])
  const entity = floor.entities.find(item => item.id === selected)
  const worker = system?.autonomousWorker
  const snapshot = office.snapshot
  const placement = snapshot?.placements.find(item => item.identityId === identityId)
  const identity = runtime.identities.find(item => item.id === identityId)
  const destinations = snapshot?.catalog.stations.filter(station => !placement || (placement.motion?.stoppedAt ? station.id === placement.motion.destinationId : snapshot.catalog.routes.some(route => route.originId === placement.stationId && route.destinationId === station.id))) ?? []
  const occupied = new Set(snapshot?.placements.filter(item => item.identityId !== identityId).flatMap(item => [item.stationId, ...(item.motion ? [item.motion.destinationId] : [])]))
  const available = destinations.filter(item => !occupied.has(item.id))
  const selectedStation = available.some(item => item.id === stationId) ? stationId : available[0]?.id ?? ''
  const disabled = office.busy || Boolean(office.pending) || !snapshot
  const inactive = !identity?.is_enabled || identity.lifecycle_state !== 'active'
  const document = useMemo<OfficeOverlayDocument>(() => ({ ...(candidate ? floor : EMPTY_FLOOR), entities: [
    ...(candidate ? floor.entities : []),
    ...(snapshot?.catalog.stations ?? []).map(station => ({ id: station.id, name: station.label, type: 'sprite_anchor' as const, geometry: { kind: 'rectangle' as const, rect: { x: station.point.x - 600, y: station.point.y - 400, width: 1200, height: 800 } }, sourceLayer: 'sprites' as const, enabled: true, interactive: false, metadata: {}, zIndex: 1 })),
    ...(snapshot?.placements ?? []).map(item => ({ id: `identity:${item.identityId}`, name: item.displayName, type: 'sprite_anchor' as const, geometry: { kind: 'rectangle' as const, rect: { x: item.position.x - 600, y: item.position.y - 400, width: 1200, height: 800 } }, sourceLayer: 'sprites' as const, enabled: true, interactive: false, metadata: {}, zIndex: 1 })),
  ] }), [candidate, floor, snapshot])
  const selectOffice = (id: string | null) => {
    setSelected(id)
    if (id?.startsWith('identity:')) setIdentityId(id.slice('identity:'.length))
    else if (id && snapshot?.catalog.stations.some(station => station.id === id)) setStationId(id)
  }
  return <>
    <header className="page-title"><div><p className="eyebrow">AI workforce · Floor 1</p><h1>Operations floor</h1><p>Explore the original office and inspect the workforce reported by the Hub.</p></div><Status value={connection}/></header>
    <div className="hub-office-layout"><section className="hub-office-scene" aria-label="Interactive office">
      <OfficeViewport active document={document} debug={false} reviewMode={candidate} selectedId={selected} hoveredId={hovered} visibleLayers={layers} onSelect={selectOffice} onHover={setHovered} onPointerOfficePoint={ignorePointer} onTransformChange={setTransform} focusRequest={focus}>{snapshot && <LiveOfficeLayer snapshot={snapshot} selectedId={selected} onSelect={selectOffice}/>}</OfficeViewport>
      <div className="office-caption"><span>Floor 1 · {Math.round(transform.scale * 100)}% · drag to pan, scroll or pinch to zoom</span><label><input type="checkbox" checked={candidate} onChange={event => { setCandidate(event.target.checked); setSelected(null); setError('') }}/> Inspect candidate geometry</label></div>
      {candidate && <section className="panel"><h2>Unverified floor registration</h2><p>The full candidate floor is not approved for live navigation. Measured registration and collision checks support only the six stations in the live office controls; other geometry remains under review.</p><div className="chips">{FLOOR1_CANDIDATE_LAYER_CONTROLS.map(control => <label key={control.category}><input type="checkbox" checked={layers.has(control.layer)} onChange={() => setLayers(previous => { const next = new Set(previous); if (next.has(control.layer)) next.delete(control.layer); else next.add(control.layer); return next })}/>{control.label}</label>)}</div><label>Inspect region<select value={selected ?? ''} onChange={event => setSelected(event.target.value || null)}><option value="">Select region</option>{floor.entities.filter(item => layers.has(item.sourceLayer)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{entity && <div><h3>{entity.name}</h3><p>{entity.type.replaceAll('_', ' ')} · {entity.accessState ? `Access: ${entity.accessState}` : 'Candidate position'}</p>{entity.seatPriority && <p>Seat priority: {entity.seatPriority === 'yellow' ? 'priority' : 'standard'}</p>}<button className="secondary" onClick={() => setFocus(value => value + 1)}>Focus selected region</button></div>}</section>}
      {error && <p role="alert">{error}</p>}
    </section><aside className="panel office-workforce"><h2>Local workforce</h2><Status value={worker?.status ?? 'unknown'}/><p>{worker?.activeExecutionCount ?? 0} executing · {worker?.reviewRequiredCount ?? 0} need review</p>
      <p className="muted">Assign a real identity to a verified desk. Moves follow the original office paths. Cross-room travel is unavailable; one move reserves the aisle until it stops or arrives.</p>
      {snapshot?.emergencyStop && <p role="alert">Emergency stop is active. Office moves remain stopped until you resume the system and explicitly continue a move.</p>}
      {office.error && <p role="alert">{office.error}</p>}
      {office.pending && <div role="status"><p>The command acknowledgement is uncertain. Refresh to inspect state or retry the same command safely.</p><button disabled={office.busy} onClick={() => void office.retry()}>Retry office command</button></div>}
      <button className="secondary" disabled={office.busy} onClick={() => void loadOffice()}>Refresh office</button>
      <label>Office identity<select value={identityId} onChange={event => { setIdentityId(event.target.value); setSelected(event.target.value ? `identity:${event.target.value}` : null) }}><option value="">Select identity</option>{runtime.identities.map(item => <option key={item.id} value={item.id}>{item.display_name}{snapshot?.placements.some(placed => placed.identityId === item.id) ? ' · placed' : ' · unplaced'}</option>)}</select></label>
      {identity && <section aria-label="Office placement controls"><h3>{identity.display_name}</h3><Status value={identity.is_enabled ? identity.lifecycle_state : 'disabled'}/>{inactive && <p>Activate this identity in Planning before assigning or moving it.</p>}
        {placement ? <><p>Activity: <strong>{placement.activity}</strong> · Movement: <strong>{placement.movementState}</strong></p><p>{snapshot?.catalog.stations.find(station => station.id === placement.stationId)?.label}{placement.motion && ` → ${snapshot?.catalog.stations.find(station => station.id === placement.motion!.destinationId)?.label}`}</p><button onClick={() => { setSelected(`identity:${identityId}`); setFocus(value => value + 1) }}>Focus identity</button></> : <label>Original sprite<select value={spriteId} onChange={event => setSpriteId(event.target.value)}>{snapshot?.catalog.spriteIds.map((id, index) => <option key={id} value={id}>Original agent {index + 1}</option>)}</select></label>}
        <label>{placement ? 'Move destination' : 'Assign desk'}<select value={selectedStation} onChange={event => setStationId(event.target.value)}>{!selectedStation && <option value="">No free verified destination</option>}{destinations.map(station => <option key={station.id} value={station.id} disabled={occupied.has(station.id)}>{station.label}{occupied.has(station.id) ? ' · occupied' : ''}</option>)}</select></label>
        <button disabled={!selectedStation} onClick={() => { setSelected(selectedStation); setFocus(value => value + 1) }}>Inspect destination</button>
        <button disabled={disabled || inactive || snapshot?.emergencyStop || !selectedStation || placement?.movementState === 'moving'} onClick={() => void office.command(identityId, { action: placement ? 'move' : 'assign', expectedVersion: placement?.version ?? snapshot?.placementVersions[identityId] ?? 0, stationId: selectedStation, ...(!placement ? { spriteId } : {}) })}>{placement ? placement.movementState === 'stopped' ? 'Continue move' : 'Move identity' : 'Assign identity'}</button>
        {placement && <><button disabled={disabled || placement.movementState !== 'moving'} onClick={() => void office.command(identityId, { action: 'stop', expectedVersion: placement.version })}>Stop movement</button><button className="secondary" disabled={disabled} onClick={() => void office.command(identityId, { action: 'release', expectedVersion: placement.version })}>Release desk assignment</button></>}
        <Link to="/runtime">Inspect authorized planning history</Link>
      </section>}
      {!runtime.identities.length && <p>No runtime identities registered. <Link to="/runtime">Set up local planning</Link> to create one.</p>}
      {runtime.actorId && <><h3>Selected identity’s runtime view</h3>{runtime.runs.map(run => <button key={run.specification.run_id} onClick={() => selectTask(run.specification.task_id)}>{tasks.find(task => task.id === run.specification.task_id)?.title ?? run.specification.task_id}<Status value={run.state}/></button>)}</>}
      <details><summary>Simulation workforce ({agents.length})</summary><p>These agents belong to the persisted demonstration workflow.</p>{agents.map(agent => <button className="office-workforce-agent" key={agent.id} onClick={() => selectAgent(agent.id)}><strong>{agent.name}</strong><Status value={agent.status}/><small>{agent.statusMessage}</small></button>)}</details>
    </aside></div>
  </>
}
