import { useState } from 'react'
import { useAppStore } from '../state/AppStore'
import type { Task } from '../types/contracts'
import { ActionDrawer } from './ActionDrawer'
import { TaskCreateForm } from './TaskCreateForm'
import { NavIcon } from './NavIcon'

export function NewTaskButton({ label = 'New task', source, disabled = false, onInspectTask, onPlanTask }: { label?: string; source?: Task; disabled?: boolean; onInspectTask?: (id: string) => void; onPlanTask?: (id: string) => void } = {}) {
  const store = useAppStore()
  const [open, setOpen] = useState(false)
  const [engaged, setEngaged] = useState(false)
  const [created, setCreated] = useState<{ task: Task; warning: string } | null>(null)
  const stale = Boolean(store.loading || store.error || store.resyncRequired || !store.system || store.connection !== 'connected')
  return <>
    <button className="primary" disabled={disabled} onClick={() => { setEngaged(true); setOpen(true) }}><NavIcon name="plus"/>{label}</button>
    <ActionDrawer open={open} title="New task" onClose={() => setOpen(false)}>
      {engaged && <><h1>Start with a clear request</h1><p className="muted">Describe the outcome you want. Creating a task saves your request; planning and execution require separate authorization.</p>
        {stale && <p role="status">Reconnect and refresh state before creating work.</p>}
        {created ? <div className="creation-success"><NavIcon name="approvals"/><h2>Task created and queued</h2><p>{created.task.title}</p>{created.warning && <p role="status">{created.warning}</p>}<button className="primary" onClick={() => { setOpen(false); requestAnimationFrame(() => (onInspectTask ?? store.selectTask)(created.task.id)) }}>Inspect task</button>{onPlanTask && <button className="primary" onClick={() => { setOpen(false); onPlanTask(created.task.id) }}>Plan this task</button>}<button className="secondary" onClick={() => setCreated(null)}>Create another task</button></div> : <fieldset disabled={stale}><TaskCreateForm source={source} onCreated={(task, warning) => setCreated({ task, warning })}/></fieldset>}
      </>}
    </ActionDrawer>
  </>
}
