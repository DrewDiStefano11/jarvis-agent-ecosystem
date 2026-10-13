import { useEffect, useRef, type KeyboardEvent } from 'react'
import { useAppStore } from '../state/AppStore'
import { Progress, Status } from './Status'
export { TaskDetails } from './TaskDetails'

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
