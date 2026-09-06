import { Link } from 'react-router-dom'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useAppStore } from '../state/AppStore'
import { Progress, Status } from './Status'

function useDialogFocus(onClose: () => void) {
  const dialogRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const closeButton = dialog?.querySelector<HTMLElement>('.close')
    ;(closeButton ?? dialog)?.focus()
    return () => previousFocus?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => !element.hidden)
    if (!focusable.length) {
      event.preventDefault()
      event.currentTarget.focus()
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }
  return { dialogRef, onKeyDown }
}

export function AgentDetails(){const {agents,departments,tasks,selectedAgentId,selectAgent}=useAppStore();const close=()=>selectAgent(null);const {dialogRef,onKeyDown}=useDialogFocus(close);const agent=agents.find(a=>a.id===selectedAgentId);if(!agent)return null
  const manager=agents.find(a=>a.id===agent.managerId);const department=departments.find(d=>d.id===agent.departmentId);const task=tasks.find(t=>t.id===agent.currentTaskId)
  return <div className="drawer-backdrop" onClick={close}><section ref={dialogRef} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-labelledby="agent-title" onKeyDown={onKeyDown} onClick={e=>e.stopPropagation()}>
    <button className="icon-button close" onClick={()=>selectAgent(null)} aria-label="Close agent details">×</button><p className="eyebrow">{agent.isTemporary?'Temporary simulated agent':'Permanent simulated agent'}</p><h2 id="agent-title">{agent.name}</h2><p>{agent.role} · {department?.name}</p><Status value={agent.status}/><Progress value={agent.progress}/><p>{agent.description}</p>
    <dl className="details-grid"><div><dt>Manager</dt><dd>{manager?.name??'None'}</dd></div><div><dt>Current task</dt><dd>{task?.title??'None'}</dd></div><div><dt>Queue</dt><dd>{agent.queuedTaskIds.length}</dd></div><div><dt>Reliability</dt><dd>{Math.round(agent.performance.reliabilityScore*100)}%</dd></div><div><dt>Office</dt><dd>{agent.office.zone} · {agent.office.deskId}</dd></div><div><dt>Manifest</dt><dd>{agent.version}</dd></div><div><dt>Deployment</dt><dd>{agent.deploymentStatus}</dd></div><div><dt>Memory</dt><dd>Fixture-only placeholder</dd></div></dl>
    <h3>Goals</h3><ul>{agent.goals.map(x=><li key={x}>{x}</li>)}</ul><h3>Capabilities</h3><div className="chips">{agent.capabilities.map(x=><span key={x}>{x}</span>)}</div><h3>Tool policy</h3><p><strong>Allowed:</strong> {agent.allowedTools.join(', ')||'None'}</p><p><strong>Denied:</strong> {agent.deniedTools.join(', ')}</p><h3>Performance</h3><p>{Math.round(agent.performance.completionRate*100)}% completion · {Math.round(agent.performance.accuracyScore*100)}% accuracy · {agent.performance.failedTaskCount} failed task(s) · {agent.performance.userCorrectionCount} correction(s)</p>
  </section></div>}

export function TaskDetails(){const {tasks,agents,approvals,artifacts,auditEvents,selectedTaskId,selectTask,runtime,action}=useAppStore();const [cancelling,setCancelling]=useState(false);const [cancelError,setCancelError]=useState('');const close=()=>selectTask(null);const {dialogRef,onKeyDown}=useDialogFocus(close);const task=tasks.find(t=>t.id===selectedTaskId);if(!task)return null
  const children=tasks.filter(t=>t.parentTaskId===task.id);const timeline=auditEvents.filter(e=>e.taskId===task.id);return <div className="drawer-backdrop" onClick={close}><section ref={dialogRef} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-labelledby="task-title" onKeyDown={onKeyDown} onClick={e=>e.stopPropagation()}>
    <button className="icon-button close" onClick={()=>selectTask(null)} aria-label="Close task details">×</button><p className="eyebrow">Task · {task.priority} priority</p><h2 id="task-title">{task.title}</h2><Status value={task.status}/><Progress value={task.progress}/><p><strong>Original request</strong><br/>{task.request}</p><p>{task.statusMessage}</p><p>Task ID: <code>{task.id}</code></p><Link to="/runtime" onClick={()=>{runtime.setTaskId(task.id);close()}}>Open planning and model results</Link>
    {task.correctionOfTaskId && <p>Corrected follow-up to <button className="link-button" onClick={() => selectTask(task.correctionOfTaskId!)}>{tasks.find(item => item.id === task.correctionOfTaskId)?.title ?? task.correctionOfTaskId}</button></p>}
    {tasks.some(item => item.correctionOfTaskId === task.id) && <><h3>Corrected follow-ups</h3><ul>{tasks.filter(item => item.correctionOfTaskId === task.id).map(item => <li key={item.id}><button className="link-button" onClick={() => selectTask(item.id)}>{item.title}</button> · {item.status}</li>)}</ul></>}
    <dl className="details-grid"><div><dt>Manager</dt><dd>{agents.find(a=>a.id===task.assignedManagerId)?.name??'Unassigned'}</dd></div><div><dt>Specialists</dt><dd>{task.assignedAgentIds.map(id=>agents.find(a=>a.id===id)?.name??id).join(', ')||'None'}</dd></div><div><dt>Team Status</dt><dd>{task.teamSelection?.status || 'Not started'}</dd></div><div><dt>Retries</dt><dd>{task.retryCount} / {task.maxRetries}</dd></div><div><dt>Blockers</dt><dd>{task.blockedBy.join(', ')||'None'}</dd></div></dl>
      {task.teamSelection && <><h3>Required Capabilities</h3><ul>{task.teamSelection.requiredCapabilities.map(c=><li key={c}>{c}</li>)}</ul>{task.teamSelection.optionalCapabilities.length > 0 && <><h4>Optional</h4><ul>{task.teamSelection.optionalCapabilities.map(c=><li key={c}>{c}</li>)}</ul></>}<h3>Team Rationale</h3><ul>{task.teamSelection.rationaleSummaries.map(r=><li key={r.agentId}><strong>{agents.find(a=>a.id===r.agentId)?.name??r.agentId}:</strong> {r.rationale}</li>)}</ul></>}
    {task.error&&<div className="callout danger"><strong>{task.error.code}</strong><br/>{task.error.message}</div>}<p><button className="danger-button" disabled={cancelling || ['completed','failed','cancelled'].includes(task.status)} onClick={async()=>{setCancelling(true);setCancelError('');try{await action(`/api/tasks/${encodeURIComponent(task.id)}/cancel`)}catch(error){setCancelError(error instanceof Error?error.message:'Cancellation failed')}finally{setCancelling(false)}}}>{cancelling?'Cancelling…':'Cancel task'}</button></p>{cancelError&&<p role="alert">{cancelError}</p>}<h3>Child tasks</h3>{children.length?<ul>{children.map(c=><li key={c.id}><button className="link-button" onClick={()=>selectTask(c.id)}>{c.title} · {c.status}</button></li>)}</ul>:<p className="muted">No child tasks.</p>}
    <h3>Approvals</h3>{task.approvalIds.map(id=>{const a=approvals.find(x=>x.id===id);return a?<p key={id}>{a.title} · <Status value={a.status}/></p>:null})}<h3>Artifacts</h3>{task.artifactIds.map(id=>{const a=artifacts.find(x=>x.id===id);return a?<div className="artifact" key={id}><strong>{a.name}</strong><p>{a.summary}</p><code>{a.simulatedPath}</code></div>:null})}{!task.artifactIds.length&&<p className="muted">No artifacts yet.</p>}
    {task.result&&<><h3>Final result</h3><div className="callout success">{task.result}</div></>}<h3>Timeline</h3>{timeline.length?<ol className="timeline">{timeline.map(e=><li key={e.id}><time>{new Date(e.timestamp).toLocaleString()}</time><strong>{e.summary}</strong><small>Sequence {e.sequenceNumber} · {e.correlationId}</small></li>)}</ol>:<p className="muted">No task events yet.</p>}
  </section></div>}
