import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import { Status } from '../components/Status'
import { OfficeViewport } from '../office-reference/components/office/OfficeViewport'
import { loadFloor1CandidateOverlay, FLOOR1_CANDIDATE_LAYER_CONTROLS } from '../office-reference/office/floor1/candidateReview'
import type { OfficeLayer, OfficeOverlayDocument, ViewTransform } from '../office-reference/office/types'
import '../styles/office.css'

const EMPTY_FLOOR: OfficeOverlayDocument = { schemaVersion: 1, source: { width: 8192, height: 5460 }, production: false, entities: [], pathNodes: [] }
const ignorePointer = () => undefined

export function Office() {
  const { agents, tasks, selectAgent, selectTask, runtime, system, lastSync, connection } = useAppStore()
  const [floor, setFloor] = useState(EMPTY_FLOOR)
  const [candidate, setCandidate] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const [layers, setLayers] = useState<ReadonlySet<OfficeLayer>>(() => new Set(['rooms']))
  const [transform, setTransform] = useState<ViewTransform>({ x: 0, y: 0, scale: 1 })
  const [focus, setFocus] = useState(0)
  const { loadIdentities } = runtime
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
  return <>
    <header className="page-title"><div><p className="eyebrow">AI workforce · Floor 1</p><h1>Operations floor</h1><p>Explore the original office and inspect the workforce reported by the Hub.</p></div><Status value={connection}/></header>
    <div className="hub-office-layout"><section className="hub-office-scene" aria-label="Interactive office">
      <OfficeViewport active document={candidate ? floor : EMPTY_FLOOR} debug={false} selectedId={selected} hoveredId={hovered} visibleLayers={layers} onSelect={setSelected} onHover={setHovered} onPointerOfficePoint={ignorePointer} onTransformChange={setTransform} focusRequest={focus}/>
      <div className="office-caption"><span>Floor 1 · {Math.round(transform.scale * 100)}% · drag to pan, scroll or pinch to zoom</span><label><input type="checkbox" checked={candidate} onChange={event => { setCandidate(event.target.checked); setSelected(null); setError('') }}/> Inspect candidate geometry</label></div>
      {candidate && <section className="panel"><h2>Unverified floor registration</h2><p>These are the prototype’s authored rooms, doors and paths. Their alignment is not approved for live worker navigation.</p><div className="chips">{FLOOR1_CANDIDATE_LAYER_CONTROLS.map(control => <label key={control.category}><input type="checkbox" checked={layers.has(control.layer)} onChange={() => setLayers(previous => { const next = new Set(previous); if (next.has(control.layer)) next.delete(control.layer); else next.add(control.layer); return next })}/>{control.label}</label>)}</div><label>Inspect region<select value={selected ?? ''} onChange={event => setSelected(event.target.value || null)}><option value="">Select region</option>{floor.entities.filter(item => layers.has(item.sourceLayer)).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{entity && <div><h3>{entity.name}</h3><p>{entity.type.replaceAll('_', ' ')} · {entity.accessState ? `Access: ${entity.accessState}` : 'Candidate position'}</p>{entity.seatPriority && <p>Seat priority: {entity.seatPriority === 'yellow' ? 'priority' : 'standard'}</p>}<button className="secondary" onClick={() => setFocus(value => value + 1)}>Focus selected region</button></div>}</section>}
      {error && <p role="alert">{error}</p>}
    </section><aside className="panel office-workforce"><h2>Local workforce</h2><Status value={worker?.status ?? 'unknown'}/><p>{worker?.activeExecutionCount ?? 0} executing · {worker?.reviewRequiredCount ?? 0} need review</p><p className="muted">Desk bindings are not configured. Identities remain unplaced until verified spatial assignments exist.</p>{runtime.identities.map(identity => <article key={identity.id} className="runtime-result"><h3>{identity.display_name}</h3><Status value={identity.lifecycle_state}/><p>{identity.agent_type} identity · {identity.is_enabled ? 'enabled' : 'disabled'}</p><Link to="/runtime">Inspect authorized planning history</Link></article>)}{!runtime.identities.length && <p>No runtime identities registered.</p>}
      {runtime.actorId && <><h3>Selected identity’s runtime view</h3>{runtime.runs.map(run => <button key={run.specification.run_id} onClick={() => selectTask(run.specification.task_id)}>{tasks.find(task => task.id === run.specification.task_id)?.title ?? run.specification.task_id}<Status value={run.state}/></button>)}</>}
      <details><summary>Simulation workforce ({agents.length})</summary><p>These agents belong to the persisted demonstration workflow.</p>{agents.map(agent => <button className="office-workforce-agent" key={agent.id} onClick={() => selectAgent(agent.id)}><strong>{agent.name}</strong><Status value={agent.status}/><small>{agent.statusMessage}</small></button>)}</details>
    </aside></div>
  </>
}
