import { useState, type FormEvent } from 'react'
import { useAppStore } from '../state/AppStore'
import { Progress, Status } from '../components/Status'
import { IdentityWorkforce } from '../components/IdentityWorkforce'

export function Agents() {
  const { agents, departments, tasks, action, selectAgent } = useAppStore()
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy) return
    const data = new FormData(event.currentTarget)
    setBusy(true); setError('')
    try {
      await action('/api/agents/temporary', { name: data.get('name'), role: data.get('role'), departmentId: data.get('department') })
      setShowForm(false)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Temporary demonstration agent creation failed') }
    finally { setBusy(false) }
  }
  return <>
    <header className="page-title"><div><p className="eyebrow">Local workforce</p><h1>Agents</h1><p>Register and manage identities for real runtime work, then assign authorized tasks in Planning.</p></div></header>
    <IdentityWorkforce />
    <section aria-labelledby="demonstration-agents">
      <div className="page-title"><div><p className="eyebrow">Five permanent operators</p><h2 id="demonstration-agents">Demonstration agents</h2><p>Seeded simulation roles and workload. These cards describe the demonstration, with separate registration above for runtime identities.</p></div><button className="secondary" disabled={busy} onClick={() => setShowForm(!showForm)}>Create temporary agent</button></div>
      {showForm && <form className="panel create-form" onSubmit={event => void create(event)} aria-label="Create demonstration agent"><label>Name<input name="name" required minLength={2} disabled={busy}/></label><label>Role<input name="role" required minLength={2} disabled={busy}/></label><label>Department<select name="department" disabled={busy}>{departments.map(department => <option value={department.id} key={department.id}>{department.name}</option>)}</select></label><button className="primary" disabled={busy}>{busy ? 'Creating…' : 'Create restricted simulation'}</button>{error && <p role="alert">{error}</p>}</form>}
      <div className="agent-grid">{agents.map(agent => <article className="agent-card" key={agent.id}><button className="card-hit" onClick={() => selectAgent(agent.id)} aria-label={`Open ${agent.name}`}/><div className="agent-heading"><span className="avatar large">{agent.name[0]}</span><div><h2>{agent.name}</h2><p>{agent.role}</p></div><Status value={agent.status}/></div><p>{departments.find(department => department.id === agent.departmentId)?.name} · {agent.isTemporary ? 'Temporary' : 'Permanent'} demonstration</p><Progress value={agent.progress}/><div className="agent-card-bottom"><span>{tasks.find(task => task.id === agent.currentTaskId)?.title ?? 'No active task'}</span><strong>{Math.round(agent.performance.reliabilityScore * 100)}% reliable</strong></div></article>)}</div>
    </section>
  </>
}
