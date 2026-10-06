import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'
import { TaskIndex } from '../components/TaskIndex'
import { TaskCreateForm } from '../components/TaskCreateForm'

export function Tasks() {
  const { tasks, agents, selectTask, runtime, loading, approvals, connection, error, resyncRequired, refresh } = useAppStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const correctionId = searchParams.get('correct')
  const source = tasks.find(task => task.id === correctionId)
  const canCorrect = source && ['under_review', 'failed', 'cancelled', 'completed'].includes(source.status)
  const [creating, setCreating] = useState(false)
  const [createdId, setCreatedId] = useState('')
  const [warning, setWarning] = useState('')
  return <div className="task-index-page">
    <header className="page-title"><div><h1>Tasks</h1><p>Stored task records include demonstration and operator requests. Inspect Planning for authorized runtime execution.</p></div><button className="primary" onClick={() => { setCreating(!creating); setSearchParams({}); setCreatedId('') }}>+ New task</button></header>
    {correctionId && !loading && !canCorrect && <p role="alert">{source ? 'This task is still active. Inspect its progress or cancel it before creating a correction.' : 'The source task is unavailable. Refresh the Hub and inspect task history.'}</p>}
    {(creating || Boolean(canCorrect)) && <TaskCreateForm key={source?.id ?? 'new'} source={canCorrect ? source : undefined} onCreated={(task, storageWarning) => { setCreatedId(task.id); setWarning(storageWarning); setCreating(false); setSearchParams({}) }}/>}
    {createdId && <div className="panel" role="status"><p>Task created and queued. <Link to="/runtime" onClick={() => runtime.setTaskId(createdId)}>Open planning for this task</Link></p>{warning && <p>{warning}</p>}</div>}
    <TaskIndex tasks={tasks} agents={agents} approvals={approvals} stale={Boolean(error || resyncRequired || connection!=='connected')} loading={loading} onInspect={selectTask} onRefresh={refresh}/>
  </div>
}
