import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { NavIcon } from '../components/NavIcon'
import { GoalPlanningControls } from '../components/GoalPlanningControls'
import { PlanningOutput } from '../components/PlanningOutput'
import '../styles/results.css'
import { useAppStore } from '../state/AppStore'
import '../styles/home.css'

const starters = [
  { icon: 'tasks', label: 'Develop', detail: 'Describe a software goal', text: 'Review my repository, identify issues, and propose safe fixes with a validation plan.' },
  { icon: 'lab', label: 'Analyze', detail: 'Ask a question about your work', text: 'Review my project schedules and prepare a discrepancy report with assumptions and open questions.' },
  { icon: 'activity', label: 'Create', detail: 'Outline a useful deliverable', text: 'Create a clear project report summarizing the goals, evidence, decisions, and next steps.' },
]
export function Home() {
  const { goals, tasks, results, selectTask, refresh: refreshState } = useAppStore()
  const [draft, setDraft] = useState(() => goals.pending?.request ?? '')
  const [planningPreferred, setPlanningPreferred] = useState(true)
  const { refresh } = goals
  const { refresh: refreshPlanning, inspect } = goals.planning
  const { refresh: refreshResults, loadTask } = results
  useEffect(() => { void refresh(); void refreshPlanning(); void refreshResults() }, [refresh, refreshPlanning, refreshResults])
  const selectedTask = tasks.find(task => task.id === goals.selected?.taskId)
  const selectedId = goals.selected?.id
  const plannedTask = goals.selected?.planning ? goals.selected.taskId : undefined
  const updatedAt = selectedTask?.updatedAt
  useEffect(() => { if (plannedTask && results.actorId) void loadTask(plannedTask) }, [plannedTask, updatedAt, results.actorId, loadTask])
  useEffect(() => { if (plannedTask && selectedId) void inspect(selectedId) }, [plannedTask, selectedId, updatedAt, inspect])
  const policy = goals.planning.policy
  const canPlan = Boolean(policy?.enabled && policy.expiresAt && !policy.unavailableReason && policy.admittedGoals < policy.maximumGoals)
  const plan = goals.pending ? Boolean(goals.pending.planningConsent) : planningPreferred && canPlan
  const admission = selectedId ? goals.planning.admissions[selectedId] : undefined
  const execution = plannedTask ? results.taskExecutions[plannedTask]?.find(item => item.result) : undefined
  const status = !goals.selected?.planning ? 'Saved draft' : selectedTask?.status === 'under_review' ? 'Awaiting your review' : selectedTask?.status === 'completed' ? 'Accepted planning output' : selectedTask?.status?.replaceAll('_', ' ') ?? 'Planning request saved'
  const text = goals.pending?.request ?? draft
  const submit = async () => {
    try { await goals.submit(text, plan); setDraft(''); await Promise.all([refreshState(), refreshPlanning()]) } catch { /* Shared state keeps exact input, scope and retry key. */ }
  }
  return <div className="conversation-home">
    <header className="home-heading"><div className="home-wordmark"><span aria-hidden="true">J</span>Jarvis <small>2.0</small></div><p>Your autonomous workspace</p></header>
    <section className="goal-welcome" aria-labelledby="goal-heading"><p className="home-kicker">A clear goal is a good place to start</p><h1 id="goal-heading">What would you like to <span>accomplish?</span></h1><p>Describe the outcome. Keep the request and its work together.</p></section>
    <div className="goal-starters" aria-label="Start a request">{starters.map(starter => <button key={starter.label} disabled={goals.busy || Boolean(goals.pending)} onClick={() => { setDraft(starter.text); document.getElementById('goal-request')?.focus() }}><NavIcon name={starter.icon}/><strong>{starter.label}</strong><small>{starter.detail}</small></button>)}</div>
    <form className="goal-composer" onSubmit={event => { event.preventDefault(); void submit() }}>
      <label htmlFor="goal-request">Describe your goal</label>
      <textarea id="goal-request" placeholder="What do you want to get done? Include the outcome and any important constraints…" value={text} maxLength={2000} rows={4} readOnly={goals.busy || Boolean(goals.pending)} onChange={event => setDraft(event.target.value)}/>
      {canPlan && <label className="goal-plan-choice"><input type="checkbox" checked={plan} disabled={goals.busy || Boolean(goals.pending)} onChange={event => setPlanningPreferred(event.target.checked)}/>Send under bounded planning consent</label>}
      <div className="composer-footer"><span>{text.length.toLocaleString()} / 2,000 characters</span><button className="primary" disabled={goals.busy || Array.from(text.trim()).length < 3} type="submit">{goals.busy ? 'Saving…' : goals.pending ? 'Retry same request' : plan ? 'Send for bounded planning' : 'Save goal'}<NavIcon name="next"/></button></div>
    </form>
    <p className="draft-policy"><span aria-hidden="true"/>{plan ? 'One bounded planning request, followed by your review. No workspace actions are authorized.' : 'This submission saves a draft. It does not queue execution.'}</p><GoalPlanningControls/>
    {goals.error && <div className="goal-error" role="alert"><strong>{goals.pending ? 'Submission could not be confirmed' : 'Conversation unavailable'}</strong><p>{goals.error}</p>{goals.pending && <p>Your original request and retry key are retained. Retry this same request to recover its outcome without creating duplicate work.</p>}</div>}
    {goals.selected && <section className="saved-conversation" aria-labelledby="conversation-heading"><div className="conversation-heading"><h2 id="conversation-heading">{goals.selected.title}</h2><button className="secondary" onClick={goals.newConversation}>New conversation</button></div>{goals.selected.messages.map(message => <article className="conversation-message" key={message.id}><span>You</span><p>{message.content}</p></article>)}<article className="goal-receipt"><div><span className="draft-badge">{status}</span><h3>Request preserved</h3><p>{goals.selected.planning ? 'One linked task and its captured planning scope hold this request.' : 'One linked task holds this draft. Saving it did not start a model request.'}</p>{selectedTask && <p className="task-status">Task status: {selectedTask.status.replaceAll('_', ' ')} · {selectedTask.statusMessage}</p>}</div><button className="secondary" onClick={() => selectTask(goals.selected!.taskId)}>Open linked task<NavIcon name="next"/></button></article></section>}
    {plannedTask && <section className="home-saved-output" aria-label="Saved goal work">
      {admission && <details><summary>Planning record</summary><p>Native state: {admission.state.replaceAll('_', ' ')}</p></details>}
      {admission?.errorCode && <p role="alert">Planning is blocked: {admission.errorCode.replaceAll('_', ' ')}. The saved request remains available.</p>}
      <div className="result-actions"><button className="secondary" onClick={() => { void refreshState(); void inspect(goals.selected!.id); if (results.actorId) void loadTask(plannedTask) }}>Refresh saved work</button>{admission && ['not_admitted', 'created', 'blocked'].includes(admission.state) && <button className="secondary" disabled={goals.planning.busy} onClick={() => void goals.planning.retry(goals.selected!.id)}>Retry captured planning admission</button>}<Link to="/inbox">Open Inbox →</Link></div>
      {!results.actorId && selectedTask?.result && <section className="reviewer-setup"><h3>Saved output is ready</h3><p>Enable the separate local reviewer to read and decide saved results. This does not grant execution permission.</p><button className="secondary" disabled={results.busy} onClick={() => void results.setup()}>Enable local result review</button></section>}
      {results.error && <p role="alert">{results.error}</p>}
      {execution?.result && <><h3>Saved planning output</h3><PlanningOutput result={execution.result}/><div className="result-actions"><Link to="/inbox">{selectedTask?.status === 'under_review' ? 'Review this output in Inbox →' : 'Open review history →'}</Link>{selectedTask?.status === 'completed' && <Link to="/library">Open accepted output in Library →</Link>}</div></>}
    </section>}
    <section className="recent-conversations" aria-labelledby="recent-goals-heading"><div className="conversation-heading"><h2 id="recent-goals-heading">Recent conversations</h2><button className="link-button" disabled={goals.loading} onClick={() => void goals.refresh()}>{goals.loading ? 'Loading…' : 'Refresh'}</button></div>{goals.historyError && <p role="alert">{goals.historyError}</p>}{!goals.loading && !goals.items.length && !goals.historyError && <div className="conversation-empty"><NavIcon name="activity"/><p>Your saved requests will appear here.</p></div>}<ul>{goals.items.map(goal => <li key={goal.id}><button aria-current={goal.id === goals.selected?.id ? 'true' : undefined} onClick={() => void goals.open(goal.id)}><span><strong>{goal.title}</strong><small>Saved {new Date(goal.createdAt).toLocaleDateString()} · {goal.planning ? 'Bounded planning' : 'Draft intake'}</small></span><NavIcon name="next"/></button></li>)}</ul>{goals.items.length >= 30 && <p>Showing the 30 most recent conversations.</p>}</section>
    <footer className="home-footer"><Link to="/mission-control">Open Mission Control</Link><span>Inspect existing work and system health</span></footer>
  </div>
}
