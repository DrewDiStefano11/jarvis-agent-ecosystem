import { useId, useRef } from 'react'
import type { Coordination } from '../types/coordination'
import { TaskDependencyGraph, executionMatchesPlan } from './TaskDependencyGraph'
import type { Decomposition } from '../types/decomposition'

export function PlannedWork({ record, error, execution = null }: { record: Decomposition | null; error: string; execution?: Coordination | null }) {
  const details = useRef<HTMLDetailsElement>(null)
  const prefix = useId()
  const hasExecution = record ? executionMatchesPlan(record, execution) : false
  const inspect = (id: string) => {
    if (details.current) details.current.open = true
    const target = document.getElementById(`${prefix}-${id}`)
    target?.focus()
    target?.scrollIntoView?.({ block: 'nearest' })
  }
  return <section aria-label="Planned work">
    <h3>Planned Work</h3>
    {error && <p role="alert">{error}</p>}
    {!record && !error && <p className="muted">Prepare planning to create a specialist work plan.</p>}
    {record && <>
      <p>Version {record.version} · {record.status.replaceAll('_', ' ')}</p>
      {!hasExecution && <p className="muted">This is planning readiness. Execution of these subtasks has not been confirmed.</p>}
      <p>{record.objectiveSummary}</p>
      {record.issues.map(issue => <div className="callout" key={issue.code + issue.affectedSubtasks.join(',')}>
        <strong>{issue.message}</strong>
        {issue.requiredCapabilities.length > 0 && <p>Required: {issue.requiredCapabilities.join(', ')}</p>}
        {issue.affectedSubtasks.length > 0 && <p>Affected work: {issue.affectedSubtasks.join(', ')}</p>}
      </div>)}
      <TaskDependencyGraph key={record.id} plan={record} execution={execution} onInspect={inspect}/>
      <details ref={details} className="subtask-details"><summary>Subtask details and deliverables</summary><ol>{record.subtasks.map(node => <li key={node.id} id={`${prefix}-${node.id}`} tabIndex={-1}>
        <h4>{node.title} · Planning readiness: {node.status}</h4>
        <p>Owner: {node.assignedAgentName} <small>({node.assignedAgentId})</small></p>
        <p>{node.description}</p>
        <p>Capabilities: {node.requiredCapabilities.join(', ')}</p>
        <p>Depends on: {node.dependsOn.map(key => record.subtasks.find(n => n.key === key)?.title ?? key).join(', ') || 'None — independently ready'}</p>
        <p><strong>Deliverable:</strong> {node.deliverable} ({node.outputType.replaceAll('_', ' ')})</p>
        <ul>{node.completionCriteria.map(criterion => <li key={criterion}>{criterion}</li>)}</ul>
        <p className="muted">{node.assignmentRationale}</p>
      </li>)}</ol></details>
    </>}
  </section>
}
