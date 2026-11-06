import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { NavIcon } from '../components/NavIcon'
import { useAppStore } from '../state/AppStore'
import '../styles/home.css'

const starters = [
  { icon: 'tasks', label: 'Develop', detail: 'Describe a software goal', text: 'Review my repository, identify issues, and propose safe fixes with a validation plan.' },
  { icon: 'lab', label: 'Analyze', detail: 'Ask a question about your work', text: 'Review my project schedules and prepare a discrepancy report with assumptions and open questions.' },
  { icon: 'activity', label: 'Create', detail: 'Outline a useful deliverable', text: 'Create a clear project report summarizing the goals, evidence, decisions, and next steps.' },
]
export function Home() {
  const { goals, tasks, selectTask } = useAppStore()
  const [draft, setDraft] = useState(() => goals.pending?.request ?? '')
  const { refresh } = goals
  useEffect(() => { void refresh() }, [refresh])
  const selectedTask = tasks.find(task => task.id === goals.selected?.taskId)
  const text = goals.pending?.request ?? draft
  const submit = async () => {
    try { await goals.submit(text); setDraft('') } catch { /* Shared state keeps input and the retry key. */ }
  }
  return <div className="conversation-home">
    <header className="home-heading"><div className="home-wordmark"><span aria-hidden="true">J</span>Jarvis <small>2.0</small></div><p>Your autonomous workspace</p></header>
    <section className="goal-welcome" aria-labelledby="goal-heading"><p className="home-kicker">A clear goal is a good place to start</p><h1 id="goal-heading">What would you like to <span>accomplish?</span></h1><p>Describe the outcome. Keep the request and its work together.</p></section>
    <div className="goal-starters" aria-label="Start a request">{starters.map(starter => <button key={starter.label} disabled={goals.busy || Boolean(goals.pending)} onClick={() => { setDraft(starter.text); document.getElementById('goal-request')?.focus() }}><NavIcon name={starter.icon}/><strong>{starter.label}</strong><small>{starter.detail}</small></button>)}</div>
    <form className="goal-composer" onSubmit={event => { event.preventDefault(); void submit() }}>
      <label htmlFor="goal-request">Describe your goal</label>
      <textarea id="goal-request" placeholder="What do you want to get done? Include the outcome and any important constraints…" value={text} maxLength={2000} rows={4} readOnly={goals.busy || Boolean(goals.pending)} onChange={event => setDraft(event.target.value)}/>
      <div className="composer-footer"><span>{text.length.toLocaleString()} / 2,000 characters</span><button className="primary" disabled={goals.busy || Array.from(text.trim()).length < 3} type="submit">{goals.busy ? 'Saving…' : goals.pending ? 'Retry same request' : 'Save goal'}<NavIcon name="next"/></button></div>
    </form>
    <p className="draft-policy"><span aria-hidden="true"/>Goals are saved as drafts. Automatic planning and execution are not enabled by submission.</p>
    {goals.error && <div className="goal-error" role="alert"><strong>{goals.pending ? 'Submission could not be confirmed' : 'Conversation unavailable'}</strong><p>{goals.error}</p>{goals.pending && <p>Your original request and retry key are retained. Retry this same request to recover its outcome without creating duplicate work.</p>}</div>}
    {goals.selected && <section className="saved-conversation" aria-labelledby="conversation-heading"><div className="conversation-heading"><h2 id="conversation-heading">{goals.selected.title}</h2><button className="secondary" onClick={goals.newConversation}>New conversation</button></div>{goals.selected.messages.map(message => <article className="conversation-message" key={message.id}><span>You</span><p>{message.content}</p></article>)}<article className="goal-receipt"><div><span className="draft-badge">Saved draft</span><h3>Request preserved</h3><p>One linked task holds this request. No model call or execution was started by saving it.</p>{selectedTask && <p className="task-status">Task status: {selectedTask.status.replaceAll('_', ' ')} · {selectedTask.statusMessage}</p>}</div><button className="secondary" onClick={() => selectTask(goals.selected!.taskId)}>Open linked task<NavIcon name="next"/></button></article></section>}
    <section className="recent-conversations" aria-labelledby="recent-goals-heading"><div className="conversation-heading"><h2 id="recent-goals-heading">Recent conversations</h2><button className="link-button" disabled={goals.loading} onClick={() => void goals.refresh()}>{goals.loading ? 'Loading…' : 'Refresh'}</button></div>{goals.historyError && <p role="alert">{goals.historyError}</p>}{!goals.loading && !goals.items.length && !goals.historyError && <div className="conversation-empty"><NavIcon name="activity"/><p>Your saved requests will appear here.</p></div>}<ul>{goals.items.map(goal => <li key={goal.id}><button aria-current={goal.id === goals.selected?.id ? 'true' : undefined} onClick={() => void goals.open(goal.id)}><span><strong>{goal.title}</strong><small>Saved {new Date(goal.createdAt).toLocaleDateString()} · Draft intake</small></span><NavIcon name="next"/></button></li>)}</ul>{goals.items.length >= 30 && <p>Showing the 30 most recent conversations.</p>}</section>
    <footer className="home-footer"><Link to="/mission-control">Open Mission Control</Link><span>Inspect existing work and system health</span></footer>
  </div>
}
