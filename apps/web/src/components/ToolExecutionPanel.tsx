import { useState } from 'react'
import { useAppStore } from '../state/AppStore'
import type { ModelExecution } from '../types/runtime'
import type { ToolScope } from '../types/toolExecution'
import { Status } from './Status'

export function ToolExecutionPanel({ execution }: { execution: ModelExecution }) {
  const { tools, system, refresh } = useAppStore()
  const [workspaceId, setWorkspaceId] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const workspace = tools.workspaces.find(item => item.workspaceId === workspaceId)
  const steps = execution.result?.steps ?? []
  const runs = tools.executions.filter(item => item.sourceExecutionId === execution.executionId)
  const scope: ToolScope | null = workspace ? {
    workspaceId, allowedTools: [...new Set(steps.map(step => step.tool))].sort(),
    readPrefixes: workspace.readPrefixes, writePrefixes: workspace.writePrefixes, maximumBytes: 65536, maximumSteps: 8,
  } : null
  const authorize = async () => {
    if (!scope || !execution.resultHash || execution.stage !== 'completed' || !reviewed || busy || system?.emergencyStop || runs.length > 0) return
    setBusy(true); setMessage('')
    try {
      const result = await tools.authorize(execution.executionId, execution.resultHash, scope)
      setMessage(`Authorized execution ${result.executionId}. The original plan remains in history.`)
      try { await refresh() }
      catch { setMessage(`Authorized execution ${result.executionId}. Status refresh failed; refresh tool progress to inspect its outcome.`) }
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Authorization failed; retry the same plan and scope to recover its acknowledgement.') }
    finally { setBusy(false) }
  }
  return <section className="tool-execution"><h4>Review workspace actions</h4>
    <p>These fixed steps run only after your authorization. Reads return observations; they do not trigger another model call or change later steps.</p>
    <ol>{steps.map((step, index) => <li key={index}><strong>{step.tool}</strong> · <code>{step.path}</code>
      {step.content != null && <details><summary>Inspect exact file content</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{step.content}</pre></details>}
      {step.expectedContentHash && <p>Replace only content matching <code>{step.expectedContentHash}</code></p>}</li>)}</ol>
    <p className="muted">Reviewed plan hash: <code>{execution.resultHash ?? 'Unavailable'}</code></p>
    <label>Authorized workspace<select value={workspaceId} disabled={busy} onChange={event => { setWorkspaceId(event.target.value); setReviewed(false) }}><option value="">Select marked workspace</option>{tools.workspaces.map(item => <option key={item.workspaceId} value={item.workspaceId} disabled={!item.ready}>{item.displayName}{item.ready ? '' : ` · ${item.reasonCode}`}</option>)}</select></label>
    {!tools.workspaces.length && <p>No workspace is configured for tool execution. Follow the local workspace setup guide.</p>}
    {scope && <p>Allow {scope.allowedTools.join(', ')}. Read under {scope.readPrefixes.join(', ') || 'no paths'}; write under {scope.writePrefixes.join(', ') || 'no paths'}. At most 8 steps and 64 KiB per file.</p>}
    <label><input type="checkbox" checked={reviewed} disabled={busy || !scope} onChange={event => setReviewed(event.target.checked)}/> I reviewed the exact actions, file contents and workspace scope.</label>
    {execution.stage !== 'completed' && <p>The plan must complete its runtime review before these actions can be authorized.</p>}
    <p><button className="primary" disabled={busy || !reviewed || !workspace?.ready || !execution.resultHash || execution.stage !== 'completed' || system?.emergencyStop || runs.length > 0} onClick={() => void authorize()}>{busy ? 'Authorizing…' : 'Authorize workspace execution'}</button></p>
    {message && <p role="status">{message}</p>}
    {runs.length > 0 && <p>This plan already has an authorized execution. Inspect its workspace execution history below.</p>}
  </section>
}

export function ToolExecutionHistory() {
  const { tools, selectTask } = useAppStore()
  const runs = tools.executions
  return <section className="panel"><h2>Workspace execution history</h2>
    {tools.error && <p role="alert">{tools.error}</p>}
    {runs.map(run => <article className="runtime-result" key={run.executionId}><h4>Workspace execution</h4><Status value={run.stage}/><p><code>{run.executionId}</code></p><button className="secondary" onClick={() => selectTask(run.taskId)}>Open execution task</button>
      {run.failureCode && <p role="status">{run.failureCode}</p>}
      <ol>{run.steps.map(step => <li key={step.stepIndex}>{step.tool} · {step.path} · {step.status}{step.failureCode && <p>{step.failureCode}</p>}{step.observation && <details><summary>Observation</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{step.observation.content ?? step.observation.entries.join('\n')}</pre></details>}</li>)}</ol>
      {run.artifacts.map(artifact => <p key={artifact.artifactId}><button className="secondary" onClick={() => void tools.openArtifact(artifact.artifactId)}>Read {artifact.relativePath}</button> · {artifact.byteCount} bytes · <code>{artifact.contentHash}</code></p>)}
    </article>)}
    {tools.artifact && runs.some(run => run.executionId === tools.artifact?.executionId) && <article><h4>{tools.artifact.relativePath}</h4><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{tools.artifact.content}</pre></article>}
    <button className="secondary" disabled={tools.loading} onClick={() => void tools.refreshTools()}>Refresh tool progress</button>
    {!runs.length && !tools.loading && !tools.error && <p>No authorized workspace execution for this task yet.</p>}
  </section>
}
