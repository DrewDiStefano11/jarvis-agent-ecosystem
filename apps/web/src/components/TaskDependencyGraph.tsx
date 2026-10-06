import { useEffect, useId, useState } from 'react'
import type { Coordination } from '../types/coordination'
import type { Decomposition, PlannedSubtask } from '../types/decomposition'
import '../styles/task-dependencies.css'

/** Never overlay a coordinator from an older plan or a different assignment. */
export function executionMatchesPlan(plan: Decomposition, execution: Coordination | null) {
  return Boolean(execution && execution.taskId === plan.taskId && execution.decompositionId === plan.id
    && execution.nodes.length === plan.subtasks.length
    && new Set(execution.nodes.map(node => node.subtaskId)).size === execution.nodes.length
    && plan.subtasks.every(node => execution.nodes.some(run => run.subtaskId === node.id && run.key === node.key && run.assignedAgentId === node.assignedAgentId)))
}

/** Bounded topological layout; malformed or cyclic data keeps the list available. */
export function dependencyLevels(nodes: PlannedSubtask[]): Map<string, number> | null {
  if (!nodes.length || nodes.length > 12 || new Set(nodes.map(node => node.key)).size !== nodes.length) return null
  const levels = new Map<string, number>()
  for (let pass = 0; pass < nodes.length; pass++) {
    for (const node of nodes) {
      if (!levels.has(node.key) && node.dependsOn.every(key => levels.has(key))) {
        levels.set(node.key, Math.max(-1, ...node.dependsOn.map(key => levels.get(key)!)) + 1)
      }
    }
  }
  return levels.size === nodes.length ? levels : null
}

export function TaskDependencyGraph({ plan, execution, onInspect }: { plan: Decomposition; execution: Coordination | null; onInspect: (id: string) => void }) {
  const [mode, setMode] = useState<'graph' | 'list'>(() => window.matchMedia('(max-width:760px)').matches ? 'list' : 'graph')
  const [zoom, setZoom] = useState(1)
  useEffect(() => {
    const query = window.matchMedia('(max-width:760px)')
    const resize = () => setMode(query.matches ? 'list' : 'graph')
    query.addEventListener('change', resize)
    return () => query.removeEventListener('change', resize)
  }, [])
  const marker = useId().replaceAll(':', '')
  const levels = dependencyLevels(plan.subtasks)
  const matched = executionMatchesPlan(plan, execution)
  const current = matched ? execution : null
  const columns = new Map<number, PlannedSubtask[]>()
  plan.subtasks.forEach(node => {
    const level = levels?.get(node.key) ?? 0
    columns.set(level, [...(columns.get(level) ?? []), node])
  })
  const rows = Math.max(1, ...Array.from(columns.values(), nodes => nodes.length))
  const width = columns.size * 304 + 32
  const height = rows * 168 + 32
  const point = (node: PlannedSubtask) => {
    const level = levels?.get(node.key) ?? 0
    const peers = columns.get(level)!
    return { x: 32 + level * 304, y: 32 + peers.indexOf(node) * 168 + (rows - peers.length) * 84 }
  }
  const content = (node: PlannedSubtask, view: 'graph' | 'list') => {
    const run = current?.nodes.find(item => item.subtaskId === node.id)
    return <><strong title={node.title}>{node.title}</strong><span id={`${marker}-${view}-${node.id}-owner`} title={node.assignedAgentName}>{node.assignedAgentName}</span><span id={`${marker}-${view}-${node.id}-status`} className={`dependency-status ds-${run?.status ?? node.status}`}>{run?.status ?? node.status}{run && run.attemptCount > 0 && <small> · Attempt {run.attemptCount}</small>}</span></>
  }
  return <section className={`task-dependencies dependency-mode-${levels ? mode : 'list'}`} aria-label="Task dependencies">
    <div className="dependency-heading"><div><h3>Task dependencies</h3><p>Plan version {plan.version} · {matched ? 'Execution matched to this plan' : 'Planning readiness'}</p></div><div className="dependency-controls" role="group" aria-label="Dependency view">
      <button aria-pressed={Boolean(levels && mode === 'graph')} disabled={!levels} onClick={() => setMode('graph')}>Graph</button><button aria-pressed={!levels || mode === 'list'} onClick={() => setMode('list')}>List</button>
    </div></div>
    {execution && !matched && <p className="dependency-notice">Execution does not match this plan. Showing planning readiness; inspect specialist execution separately.</p>}
    {!levels && plan.subtasks.length > 0 && <p className="dependency-notice">Graph unavailable for these dependencies. The full list remains inspectable.</p>}
    {levels && <div className="dependency-graph-view">
      <div className="dependency-zoom" role="group" aria-label="Graph zoom"><button aria-label="Zoom out dependencies" disabled={zoom <= .7} onClick={() => setZoom(value => Math.max(.7, value - .1))}>−</button><button aria-label="Reset dependency zoom" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button><button aria-label="Zoom in dependencies" disabled={zoom >= 1.4} onClick={() => setZoom(value => Math.min(1.4, value + .1))}>+</button><small>Arrows point from required input to dependent work. Scroll to pan.</small></div>
      <div className="dependency-scroll" tabIndex={0} role="region" aria-label="Dependency graph, scroll to pan">
        <div className="dependency-extent" style={{ width: width * zoom, height: height * zoom }}><div className="dependency-canvas" style={{ width, height, left: `calc(50% - ${width * zoom / 2}px)`, transform: `scale(${zoom})` }}>
          <svg width={width} height={height} aria-hidden="true"><defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="#9096ac"/></marker></defs>{plan.subtasks.flatMap(node => node.dependsOn.map(key => {
            const input = plan.subtasks.find(item => item.key === key)!
            const from = point(input), to = point(node)
            return <path key={`${key}-${node.key}`} d={`M ${from.x + 248} ${from.y + 68} C ${from.x + 276} ${from.y + 68}, ${to.x - 28} ${to.y + 68}, ${to.x - 4} ${to.y + 68}`} fill="none" stroke="#9096ac" strokeWidth="1.5" markerEnd={`url(#${marker})`}/>
          }))}</svg>
          {plan.subtasks.map(node => { const position = point(node); return <button className={`dependency-node dn-${current?.nodes.find(item => item.subtaskId === node.id)?.status ?? node.status}`} key={node.id} style={{ left: position.x, top: position.y }} aria-describedby={`${marker}-graph-${node.id}-owner ${marker}-graph-${node.id}-status`} aria-label={`Inspect subtask ${node.title}`} onClick={() => onInspect(node.id)}>{content(node, 'graph')}</button> })}
        </div></div>
      </div>
    </div>}
    <ol className="dependency-list">{plan.subtasks.map(node => <li key={node.id} className={`dn-${current?.nodes.find(item => item.subtaskId === node.id)?.status ?? node.status}`}><button aria-describedby={`${marker}-list-${node.id}-owner ${marker}-list-${node.id}-status ${marker}-list-${node.id}-dependencies`} aria-label={`Inspect subtask ${node.title}`} onClick={() => onInspect(node.id)}>{content(node, 'list')}</button><p id={`${marker}-list-${node.id}-dependencies`}>{node.dependsOn.length ? `Depends on: ${node.dependsOn.map(key => plan.subtasks.find(item => item.key === key)?.title ?? key).join(', ')}` : 'No dependencies'}</p></li>)}</ol>
    {current && <div className="dependency-synthesis"><strong>Manager synthesis · {current.synthesis.status}</strong><span>Coordinator: {current.status}</span></div>}
    {!plan.subtasks.length && <p>No subtasks in this persisted plan.</p>}
  </section>
}
