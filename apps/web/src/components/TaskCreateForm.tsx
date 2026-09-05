import { useEffect, useRef, useState } from 'react'
import { useAppStore } from '../state/AppStore'
import { acknowledgeTaskCreation, prepareTaskCreation, submitTaskCreation, type TaskCreationAttempt } from '../state/taskCreation'
import type { Task } from '../types/contracts'

export function TaskCreateForm({ source, projectId, onCreated }: { source?: Task; projectId?: string; onCreated: (task: Task, warning: string) => void }) {
  const { refresh } = useAppStore()
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [title, setTitle] = useState(source ? `${source.title.slice(0, 148)} (corrected)` : '')
  const [description, setDescription] = useState(source?.description ?? '')
  const [priority, setPriority] = useState<Task['priority']>(source?.priority ?? 'medium')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<TaskCreationAttempt | null>(null)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')
  const create = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const attempt = pending ?? await prepareTaskCreation({ title, description, priority,
        ...(projectId ? { projectId } : {}),
        ...(source ? { correctionOfTaskId: source.id } : {}) })
      setPending(attempt)
      setWarning(attempt.warning)
      const created = await submitTaskCreation(attempt)
      const storageWarning = acknowledgeTaskCreation(attempt)
      await refresh()
      if (mounted.current) onCreated(created, storageWarning)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Task creation failed') }
    finally { setBusy(false) }
  }
  return <form className="panel task-create-form" onSubmit={event => void create(event)}>
    {source && <><h2>Correct task input</h2><p>Use the review findings to revise this request. The new task keeps a link to <strong>{source.title}</strong>. Its original input, result and review remain in history.</p></>}
    <label>Title<input name="title" minLength={3} maxLength={160} required value={title} disabled={busy || Boolean(pending)} onChange={event => setTitle(event.target.value)}/></label>
    <label className="task-description">Description<textarea aria-label="Description" name="description" minLength={3} maxLength={2000} required value={description} disabled={busy || Boolean(pending)} onChange={event => setDescription(event.target.value)}/></label>
    <label>Priority<select name="priority" value={priority} disabled={busy || Boolean(pending)} onChange={event => setPriority(event.target.value as Task['priority'])}>{['medium', 'high', 'urgent', 'low'].map(value => <option key={value}>{value}</option>)}</select></label>
    <button className="primary" disabled={busy}>{busy ? 'Creating…' : pending ? 'Retry creation' : source ? 'Create corrected task' : 'Create task'}</button>
    {error && <p role="alert">{error}</p>}{warning && <p role="status">{warning}</p>}
    {pending && error && <p>The outcome may be uncertain. Retry creation with this same request to retrieve the original task. After a reload, submitting identical fields reuses its saved retry key. Inspect the task list before changing the request.</p>}
    <p>Created tasks stay queued until you explicitly prepare and submit them for local planning. Creating a correction does not approve or resume the original run.</p>
  </form>
}
