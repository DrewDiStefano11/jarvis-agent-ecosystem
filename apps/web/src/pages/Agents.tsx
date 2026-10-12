import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useAppStore } from '../state/AppStore'
import { Progress, Status } from '../components/Status'
import { IdentityWorkforce } from '../components/IdentityWorkforce'
import { AgentCatalog } from '../components/AgentCatalog'
import { demonstrationStatuses, filterDemonstrationAgents, type DemonstrationKind } from './agents/demonstration'
import '../styles/agents-workspace.css'

export function Agents() {
  const { agents, departments, tasks, action, selectAgent, loading, error, runtime } = useAppStore()
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [departmentId, setDepartmentId] = useState('')
  const [kind, setKind] = useState<DemonstrationKind>('')
  const formRef = useRef<HTMLFormElement>(null)
  const statuses = useMemo(() => [...new Set([...demonstrationStatuses(agents), ...(status ? [status] : [])])].sort(), [agents, status])
  const visible = useMemo(
    () => filterDemonstrationAgents(agents, departments, tasks, { query, status, departmentId, kind }),
    [agents, departments, tasks, query, status, departmentId, kind],
  )
  const filtersActive = Boolean(query.trim() || status || departmentId || kind)
  useEffect(() => {
    if (!showForm) return
    formRef.current?.querySelector<HTMLInputElement>('input[name="name"]')?.focus()
  }, [showForm])

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy) return
    const data = new FormData(event.currentTarget)
    setBusy(true); setFormError(''); setMessage('')
    try {
      await action('/api/agents/temporary', { name: data.get('name'), role: data.get('role'), departmentId: data.get('department') })
      setShowForm(false)
      setMessage('Temporary demonstration agent saved. It is not a runtime identity and it is not available for planning.')
    } catch (caught) { setFormError(caught instanceof Error ? caught.message : 'Temporary demonstration agent creation failed') }
    finally { setBusy(false) }
  }

  return <div className="agents-page">
    <header className="page-title">
      <div>
        <p className="eyebrow">Local workforce</p>
        <h1>Agents</h1>
        <p>Runtime identities, imported catalog definitions, and demonstration agents are separate. Only runtime identities can be assigned real planning work.</p>
      </div>
    </header>
    <nav className="agents-map" aria-label="Workforce sections">
      <a href="#runtime-identities"><strong>Runtime identities</strong><span>Durable registry used by Planning and the office. {runtime.identities.length} loaded.</span></a>
      <a href="#workforce-catalog"><strong>Imported catalog</strong><span>Specialist and skill definitions. Not registered identities.</span></a>
      <a href="#demonstration-agents"><strong>Demonstration agents</strong><span>Simulator roles and workload. Not runtime identities.</span></a>
    </nav>
    {loading && !agents.length && <p role="status">Loading the workforce snapshotâ€¦</p>}
    {error && !formError && <p className="callout danger" role="alert">{error} Demonstration cards below may be missing or stale.</p>}

    <section id="runtime-identities" className="agents-anchor" aria-labelledby="runtime-identities-title">
      <p className="agents-kind agents-kind-runtime">Real registry</p>
      <h2 id="runtime-identities-title">Runtime identities</h2>
      <p className="agents-section-note">These are the identities Planning and the office can use. Registration leaves an identity provisioned. It does not grant tools, permissions, or a task.</p>
      <IdentityWorkforce />
    </section>

    <section id="workforce-catalog" className="agents-anchor" aria-labelledby="workforce-catalog-title">
      <p className="agents-kind agents-kind-catalog">Imported definitions</p>
      <h2 id="workforce-catalog-title">Catalog boundary</h2>
      <p className="agents-section-note">The catalog stores imported specialist and skill definitions. Browsing it does not create a runtime identity. Approval and activation stay on the existing catalog controls and still follow their review rules.</p>
      <AgentCatalog />
    </section>

    <section id="demonstration-agents" aria-labelledby="demonstration-agents-title">
      <div className="page-title">
        <div>
          <p className="agents-kind agents-kind-demo">Simulator only</p>
          <p className="eyebrow">Five permanent operators</p>
          <h2 id="demonstration-agents-title">Demonstration agents</h2>
          <p>Seeded simulation roles and workload. These cards describe the demonstration. They are not the runtime identities registered above.</p>
        </div>
        <button className="secondary" type="button" aria-expanded={showForm} aria-controls="demo-create" disabled={busy} onClick={() => { setShowForm(value => !value); setFormError('') }}>
          {showForm ? 'Hide temporary agent form' : 'Create temporary agent'}
        </button>
      </div>
      {message && <p className="callout success" role="status">{message}</p>}
      {showForm && <form ref={formRef} id="demo-create" className="panel create-form demo-form" onSubmit={event => void create(event)} aria-label="Create demonstration agent">
        <p>This adds a restricted simulation agent to the demonstration. It does not register a runtime identity, and it cannot be selected for planning.</p>
        <label>Name<input name="name" required minLength={2} maxLength={60} disabled={busy} /></label>
        <label>Role<input name="role" required minLength={2} maxLength={100} disabled={busy} /></label>
        <label>Department<select name="department" required disabled={busy || departments.length === 0}>{departments.map(department => <option value={department.id} key={department.id}>{department.name}</option>)}</select></label>
        {!departments.length && <p role="status">No demonstration departments are loaded, so a temporary agent cannot be placed.</p>}
        <button className="primary" disabled={busy || departments.length === 0}>{busy ? 'Creatingâ€¦' : 'Create restricted simulation'}</button>
        {formError && <p role="alert">{formError}</p>}
      </form>}
      <div className="filters demo-toolbar">
        <label>Find a demonstration agent<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, role, or description" autoComplete="off" /></label>
        <label>Recorded status<select aria-label="Demonstration status" value={status} onChange={event => setStatus(event.target.value)}><option value="">All statuses</option>{statuses.map(item => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select></label>
        <label>Department<select aria-label="Demonstration department" value={departmentId} onChange={event => setDepartmentId(event.target.value)}><option value="">All departments</option>{departments.map(department => <option key={department.id} value={department.id}>{department.name}</option>)}</select></label>
        <label>Record<select aria-label="Demonstration record" value={kind} onChange={event => setKind(event.target.value as DemonstrationKind)}><option value="">Permanent and temporary</option><option value="permanent">Permanent demonstration</option><option value="temporary">Temporary demonstration</option></select></label>
      </div>
      <p className="demo-meta" aria-live="polite">
        <span>Showing {visible.length} of {agents.length} demonstration agents</span>
        {filtersActive && <button type="button" className="secondary" onClick={() => { setQuery(''); setStatus(''); setDepartmentId(''); setKind('') }}>Clear demonstration filters</button>}
      </p>
      {!loading && !agents.length && <p>No demonstration agents are in this snapshot.</p>}
      {agents.length > 0 && !visible.length && <p>No demonstration agents match this filter.</p>}
      <div className="agent-grid demo-list">{visible.map(agent => {
        const department = departments.find(item => item.id === agent.departmentId)?.name ?? 'Unknown department'
        const task = tasks.find(item => item.id === agent.currentTaskId)
        return <article className="agent-card demo-card" key={agent.id} aria-label={`${agent.name} demonstration`}>
          <button className="card-hit" type="button" onClick={() => selectAgent(agent.id)} aria-label={`Open ${agent.name}`} />
          <div className="agent-heading"><span className="avatar large" aria-hidden="true">{agent.name[0] ?? '?'}</span><div><h2>{agent.name}</h2><p>{agent.role}</p></div><Status value={agent.status} /></div>
          <p>{department} Â· {agent.isTemporary ? 'Temporary' : 'Permanent'} demonstration Â· not a runtime identity</p>
          {agent.description && <p>{agent.description}</p>}
          <p className="demo-facts"><span>{agent.capabilities.length ? agent.capabilities.join(', ') : 'No capabilities recorded'}</span></p>
          <Progress value={agent.progress} label="Simulated progress" />
          <div className="agent-card-bottom"><span>{task?.title ?? 'No active task'}</span><strong>Simulated reliability {Math.round(agent.performance.reliabilityScore * 100)}%</strong></div>
        </article>
      })}</div>
    </section>
  </div>
}
