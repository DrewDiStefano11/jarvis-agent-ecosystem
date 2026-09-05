import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { TaskCreateForm } from '../components/TaskCreateForm'
import { Empty, Progress, Status } from '../components/Status'
import { useAppStore } from '../state/AppStore'

export function BusinessLab() {
  const { tasks, runtime, selectTask } = useAppStore()
  const [creating, setCreating] = useState(false)
  const [message, setMessage] = useState('')
  const { loadIdentities } = runtime
  useEffect(() => { void loadIdentities().catch(() => undefined) }, [loadIdentities])
  const objectives = tasks.filter(task => task.projectId === 'business-lab')
  return <>
    <header className="page-title"><div><p className="eyebrow">Objectives and reports</p><h1>Business Lab</h1><p>Develop an objective with your local workforce, review its proposed actions, and retain the results.</p></div><button className="primary" onClick={() => setCreating(value => !value)}>New objective</button></header>
    {creating && <TaskCreateForm projectId="business-lab" onCreated={(task, warning) => { runtime.setTaskId(task.id); setCreating(false); setMessage(`Objective saved. Open its workspace to prepare and queue a plan.${warning ? ` ${warning}` : ''}`) }}/>}
    {message && <p role="status">{message}</p>}
    <section className="panel"><h2>A bounded workflow</h2><p>Supply the facts and desired report in your objective. Open its workspace, prepare a local planner, and queue a workspace action plan. Inspect the proposed file contents, then explicitly authorize a configured workspace. Reading files returns observations; automatic research and adaptive follow-up from those reads are not implemented.</p><Link to="/agents">Manage workforce identities</Link></section>
    <section className="task-list">{objectives.map(task => <article className="task-card" key={task.id}>
      <div className="task-top"><h2>{task.title}</h2><Status value={task.status}/></div><p>{task.description}</p><p>{task.statusMessage}</p><Progress value={task.progress}/>
      <p>Participating agents: {task.assignedAgentIds.map(id => runtime.identities.find(identity => identity.id === id)?.display_name ?? id).join(', ') || 'No agent assigned yet'}</p>
      <p><Link to="/runtime?mode=workspace" onClick={() => runtime.setTaskId(task.id)}>Open objective workspace</Link> · <button className="secondary" onClick={() => selectTask(task.id)}>Inspect task history</button></p>
      {task.correctionOfTaskId && <p>Corrected follow-up · <button className="secondary" onClick={() => selectTask(task.correctionOfTaskId!)}>Inspect original objective</button></p>}
    </article>)}{!objectives.length && <Empty>Create your first objective. It stays queued until you explicitly prepare and launch its plan.</Empty>}</section>
  </>
}
