import { lazy, Suspense, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import { TaskIndex } from '../components/TaskIndex'
import { TaskCreateForm } from '../components/TaskCreateForm'
import { ActionDrawer } from '../components/ActionDrawer'
import { NavIcon } from '../components/NavIcon'

const Planning = lazy(() => import('./Runtime').then(module => ({ default: module.Runtime })))
const ApprovalInbox = lazy(() => import('./Approvals').then(module => ({ default: module.Approvals })))

export function Tasks() {
  const { tasks, agents, selectTask, runtime, system, loading, approvals, connection, error, resyncRequired, refresh } = useAppStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const correctionId = searchParams.get('correct')
  const source = tasks.find(task => task.id === correctionId)
  const canCorrect = source && ['under_review', 'failed', 'cancelled', 'completed'].includes(source.status)
  const creating = searchParams.get('create') === '1'
  const activeEditorKey = canCorrect ? source.id : creating ? 'new' : null
  const [editorKey, setEditorKey] = useState(activeEditorKey)
  const [editorGeneration, setEditorGeneration] = useState(0)
  // The editor identity is view state: retain one in-memory draft when its URL closes.
  if (activeEditorKey && editorKey !== activeEditorKey) setEditorKey(activeEditorKey)
  const editorSource = editorKey && editorKey !== 'new' ? tasks.find(task => task.id === editorKey) : undefined
  const [createdId, setCreatedId] = useState('')
  const [warning, setWarning] = useState('')
  const [planning, setPlanning] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const stale = Boolean(loading || !system || error || resyncRequired || connection !== 'connected')
  const plan = (id: string) => { runtime.setTaskId(id); setPlanning(true) }
  const inspectFromDrawer = (id: string) => { setPlanning(false); setReviewing(false); requestAnimationFrame(() => selectTask(id)) }
  const openCreation = () => { setPlanning(false); setSearchParams({ create: '1' }); setCreatedId('') }
  const correct = (id: string) => { setPlanning(false); setSearchParams({ correct: id }); setCreatedId('') }
  return <div className="task-index-page">
    <header className="page-title"><div><h1>Tasks</h1><p>Requests, progress, and saved outcomes.</p></div><button className="primary" onClick={openCreation}><NavIcon name="plus"/>New task</button></header>
    {correctionId && !loading && !canCorrect && <p className="task-index-notice" role="alert">{source ? 'This task is still active. Inspect its progress or cancel it before creating a correction.' : 'The source task is unavailable. Refresh the Hub and inspect task history.'}</p>}
    {createdId && <div className="task-created-banner"><NavIcon name="approvals"/><div><strong role="status">Task created and queued</strong><p>{tasks.find(task => task.id === createdId)?.title ?? createdId}</p>{warning && <p role="status">{warning}</p>}<small>Creating a request does not authorize execution.</small></div><button className="primary" onClick={() => plan(createdId)}>Open planning for this task</button><button className="secondary" onClick={() => selectTask(createdId)}>Inspect task</button><button className="secondary" aria-label="Dismiss creation confirmation" onClick={() => setCreatedId('')}><NavIcon name="close"/></button></div>}
    <TaskIndex tasks={tasks} agents={agents} identities={runtime.identities} approvals={approvals} confirmed={Boolean(system)} stale={stale} loading={loading} onInspect={selectTask} onRefresh={refresh} onPlan={plan} onCorrect={correct} onApprovals={() => setReviewing(true)}/>
    <ActionDrawer open={creating || Boolean(canCorrect)} title={editorKey && editorKey !== 'new' ? 'Correct task input' : 'New task'} onClose={() => setSearchParams({})}>
      {editorKey && <><h1>{canCorrect ? 'Revise the request' : 'Start with a clear request'}</h1><p className="muted">Save your desired outcome. Planning and execution require separate authorization.</p>{stale && <p role="status">Reconnect and refresh state before creating work.</p>}<fieldset disabled={stale}><TaskCreateForm key={`${editorKey}:${editorGeneration}`} source={editorSource} onCreated={(task, storageWarning) => { setCreatedId(task.id); setWarning(storageWarning); setEditorGeneration(value => value + 1); setSearchParams({}) }}/></fieldset></>}
    </ActionDrawer>
    <ActionDrawer open={planning} title="Plan this task" onClose={() => setPlanning(false)}><Suspense fallback={<p role="status">Loading planning...</p>}>{planning && <Planning onInspectTask={inspectFromDrawer} onCreateTask={openCreation} onCorrectTask={correct}/>}</Suspense></ActionDrawer>
    <ActionDrawer open={reviewing} title="Review task approvals" onClose={() => setReviewing(false)}><Suspense fallback={<p role="status">Loading approvals...</p>}>{reviewing && <ApprovalInbox onInspectTask={inspectFromDrawer} onOpenPlanning={() => { setReviewing(false); setPlanning(true) }}/>}</Suspense></ActionDrawer>
  </div>
}
