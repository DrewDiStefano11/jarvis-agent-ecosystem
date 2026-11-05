import { useState, type FormEvent } from 'react'
import { useRemoteOperator } from '../state/RemoteOperatorStore'
import { RemoteRuntimeControls } from '../components/RemoteRuntimeControls'
import { isSeededGoal } from '../state/goalProvenance'

export function RemoteOperation() {
  const store = useRemoteOperator()
  const [token, setToken] = useState(''), [title, setTitle] = useState('')
  const [description, setDescription] = useState(''), [notice, setNotice] = useState('')
  const [signing, setSigning] = useState(false)
  const login = async (event: FormEvent) => {
    event.preventDefault(); setSigning(true)
    const credential = token; setToken('')
    try { await store.login(credential) } catch { /* shared state explains failure */ }
    finally { setSigning(false) }
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setNotice('')
    try {
      const task = await store.submitGoal(title, description)
      if (!task) return
      setTitle(''); setDescription(''); setNotice('Goal recorded: ' + task.id + '. Review its status below.')
    } catch { setNotice('Submission outcome needs inspection. A retry of the same form reuses its request key.') }
  }
  const control = async (operation: 'stop' | 'resume' | 'cancel', taskId?: string) => {
    if (!window.confirm(operation === 'stop' ? 'Emergency stop all work?'
      : operation === 'resume' ? 'Resume eligible work under existing permissions?' : 'Cancel this goal?')) return
    try { await store.control(operation, taskId) } catch { /* shared state explains failure */ }
  }
  return <main className="remote-shell">
    <header className="remote-header"><div><p className="eyebrow">JARVIS</p><h1>Remote operation</h1></div>
      {store.session && <button disabled={store.authBusy || store.busy} onClick={() => void store.logout().catch(() => undefined)}>Sign out</button>}</header>
    {store.error && <p className="alert" role="alert">{store.error}</p>}
    {store.restoring ? <p role="status">Checking browser session…</p> : !store.session ?
      <section className="panel"><h2>Operator sign-in</h2><p>Use the configured operator credential over this trusted HTTPS connection.</p>
        <form onSubmit={event => void login(event)}><label>Operator credential<input type="password" value={token}
          onChange={event => setToken(event.target.value)} autoComplete="off" minLength={43} maxLength={128}
          required spellCheck={false}/></label><button className="primary" disabled={signing || store.authBusy}>
          {signing ? 'Signing in…' : 'Sign in'}</button></form></section> : <>
      <section className="panel"><div className="row"><div><h2>System state</h2>
        <p>{store.status?.emergencyStop ? 'Emergency stop active' : store.status ? 'Emergency stop clear' : 'State unavailable'}</p>
        <p>Workflow: {store.status?.simulatorStatus ?? 'unavailable'} · Recovery: {store.status?.recoveryStatus ?? 'unavailable'}</p>
        <small>Last synchronized: {store.lastSync ? new Date(store.lastSync).toLocaleTimeString() : 'never'}. Visible pages refresh every 15 seconds while connected.</small></div>
        <div className="actions"><button onClick={() => void store.refresh()}>Refresh recorded state</button>
          <button className="danger" disabled={store.busy}
            onClick={() => void control('stop')}>Emergency stop</button>
          <button disabled={store.busy || !!store.error || !store.status?.emergencyStop}
            onClick={() => void control('resume')}>Resume system</button></div></div></section>
      <section className="panel"><h2>Submit a goal</h2><p>Submission records queued work. Execution follows the existing capability, runtime and approval rules.</p>
        <form onSubmit={event => void submit(event)}><label>Goal title<input value={title} minLength={3} maxLength={160}
          required onChange={event => setTitle(event.target.value)}/></label><label>Objective<textarea value={description}
          minLength={3} maxLength={2000} required rows={4} onChange={event => setDescription(event.target.value)}/></label>
          <button className="primary" disabled={store.busy}>{store.busy ? 'Awaiting acknowledgement…' : 'Record goal'}</button></form>
        {notice && <p role="status">{notice}</p>}</section>
      <div className="remote-columns"><section className="panel"><h2>Recorded goals</h2>
        {store.goals.length === 0 && <p>No accessible goals on this page.</p>}
        <ul className="goal-list">{store.goals.map(task => <li key={task.id}><button
          aria-pressed={store.selectedId === task.id} onClick={() => void store.selectGoal(task.id)}>
          <strong>{task.title}</strong>{isSeededGoal(task.id) && <small>Seeded demonstration</small>}
          <span>{task.status} · {task.progress}%</span></button></li>)}</ul>
        <div className="actions"><button disabled={store.offset === 0} onClick={() => void store.loadPage(Math.max(0, store.offset - 20))}>Previous goals</button>
          <button disabled={store.nextOffset === null} onClick={() => void store.loadPage(store.nextOffset!)}>Next goals</button></div>
        <h3>Accessible agents · up to 50</h3><ul>{store.agents.map(agent => <li key={agent.id}>{agent.display_name} · {agent.lifecycle_state}</li>)}</ul>
      </section><section className="panel"><h2>Goal evidence</h2>
        {store.selectedId && !store.detail ? <p role="status">Loading recorded goal evidence…</p> : !store.detail ? <p>Select a goal to inspect its history and output.</p> : <>
          <h3>{store.detail.task.title}</h3><p>{store.detail.task.statusMessage}</p>
          {isSeededGoal(store.detail.task.id) && <p>This built-in demonstration record is not evidence of a completed operator objective.</p>}
          <p>Assigned agents: {store.detail.task.assignedAgentIds.join(', ') || 'None recorded'}</p>
          {store.detail.task.error && <p role="alert">{store.detail.task.error.code}: {store.detail.task.error.message}</p>}
          <button disabled={store.busy || !!store.error || ['completed', 'cancelled'].includes(store.detail.task.status)}
            onClick={() => void control('cancel', store.detail!.task.id)}>Cancel goal</button>
          <h3>Recorded result</h3><pre>{store.detail.task.result ?? 'No completed result recorded.'}</pre>
          <h3>Recent audit history · up to 20</h3><ol>{store.detail.audit.items.map(event =>
            <li key={event.id}><time>{new Date(event.timestamp).toLocaleString()}</time> {event.summary}</li>)}</ol>
          <details><summary>Recorded decomposition</summary><pre>{store.detail.graph ? JSON.stringify(store.detail.graph, null, 2) : 'No decomposition recorded.'}</pre></details>
        </>}
      </section></div><RemoteRuntimeControls/></>}
  </main>
}
