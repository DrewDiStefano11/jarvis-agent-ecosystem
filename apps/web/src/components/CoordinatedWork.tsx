import type { Coordination } from '../types/coordination'

export function CoordinatedWork({ record, error }: { record: Coordination | null; error: string }) {
  if (!record && !error) return null
  return <section aria-label="Specialist execution">
    <h3>Specialist execution</h3>
    {error && <p role="alert">{error}</p>}
    {record && <>
      <p>Coordinator: {record.status}</p>
      {record.blockedReason && <p role="status">Operator reconciliation required: {record.blockedReason.replaceAll('_', ' ')}</p>}
      <ol>{record.nodes.map(node => <li key={node.subtaskId}>
        <h4>{node.key} · {node.status}</h4>
        <p>Owner: {node.assignedAgentId} · Attempts: {node.attemptCount} / 3</p>
        {node.retryEligibleAt && <p>Retry eligible: {new Date(node.retryEligibleAt).toLocaleString()}</p>}
        {node.failureDetail && <p>{node.failureDetail}</p>}
        {node.resultSummary && <p>{node.resultSummary}</p>}
        {node.model && <small>{node.provider} / {node.model}</small>}
      </li>)}</ol>
      <h4>Manager synthesis · {record.synthesis.status}</h4>
      <p>Attempts: {record.synthesis.attemptCount} / 2</p>
      {record.synthesis.failureDetail && <p>{record.synthesis.failureDetail}</p>}
      {record.synthesis.summary && <><h4>{record.status === 'completed' ? 'Final result' : 'Accepted synthesis'}</h4><p>{record.synthesis.summary}</p><small>{record.synthesis.inputSubtaskIds.length} validated specialist results</small></>}
    </>}
  </section>
}
