import type { Decomposition } from '../types/decomposition'

export function PlannedWork({ record, error }: { record: Decomposition | null; error: string }) {
  return <section aria-label="Planned work">
    <h3>Planned Work</h3>
    {error && <p role="alert">{error}</p>}
    {!record && !error && <p className="muted">Prepare planning to create a specialist work plan.</p>}
    {record && <>
      <p>Version {record.version} · {record.status.replaceAll('_', ' ')}</p>
      <p className="muted">This is planned work. Specialists have not executed these subtasks.</p>
      <p>{record.objectiveSummary}</p>
      {record.issues.map(issue => <div className="callout" key={issue.code + issue.affectedSubtasks.join(',')}>
        <strong>{issue.message}</strong>
        {issue.requiredCapabilities.length > 0 && <p>Required: {issue.requiredCapabilities.join(', ')}</p>}
        {issue.affectedSubtasks.length > 0 && <p>Affected work: {issue.affectedSubtasks.join(', ')}</p>}
      </div>)}
      <ol>{record.subtasks.map(node => <li key={node.id}>
        <h4>{node.title} · {node.status}</h4>
        <p>Owner: {node.assignedAgentName} <small>({node.assignedAgentId})</small></p>
        <p>{node.description}</p>
        <p>Capabilities: {node.requiredCapabilities.join(', ')}</p>
        <p>Depends on: {node.dependsOn.map(key => record.subtasks.find(n => n.key === key)?.title ?? key).join(', ') || 'None — independently ready'}</p>
        <p><strong>Deliverable:</strong> {node.deliverable} ({node.outputType.replaceAll('_', ' ')})</p>
        <ul>{node.completionCriteria.map(criterion => <li key={criterion}>{criterion}</li>)}</ul>
        <p className="muted">{node.assignmentRationale}</p>
      </li>)}</ol>
    </>}
  </section>
}
