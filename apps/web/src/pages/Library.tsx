import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { PlanningOutput } from '../components/PlanningOutput'
import { useAppStore } from '../state/AppStore'
import '../styles/results.css'

export function Library() {
  const { results, selectTask } = useAppStore()
  const { refresh } = results
  useEffect(() => { void refresh() }, [refresh])
  return <div className="results-workspace"><header className="results-heading"><div><p className="eyebrow">Saved outcomes</p><h1>Library</h1><p>Accepted planning outputs with their original content and review evidence.</p></div><button className="secondary" disabled={results.loading} onClick={() => void refresh()}>{results.loading ? 'Refreshing…' : 'Refresh Library'}</button></header>
    {results.error && <p className="result-error" role="alert">{results.error}</p>}
    {!results.actorId && !results.loading && <section className="reviewer-setup"><h2>Local reviewer setup required</h2><p>Enable local result review to read accepted task outputs.</p><Link to="/inbox">Open Inbox →</Link></section>}
    {results.actorId && !results.loading && !results.artifacts.length && !results.error && <section className="results-empty"><h2>Your accepted outputs will appear here</h2><p>Review a saved planning result in Inbox to add its original JSON output.</p><Link to="/inbox">Open Inbox →</Link></section>}
    <div className="result-review-list">{results.artifacts.map(artifact => <article className="result-review-card" key={artifact.id}><header><div><span className="result-status accepted">Accepted output</span><h2>{artifact.name}</h2><p>{artifact.summary}</p></div></header><dl className="library-evidence"><div><dt>Accepted</dt><dd>{new Date(artifact.review.reviewedAt).toLocaleString()}</dd></div><div><dt>Original SHA-256</dt><dd>{artifact.resultHash}</dd></div><div><dt>Review</dt><dd>{artifact.review.id}</dd></div></dl><div className="result-actions"><button className="secondary" onClick={() => void results.preview(artifact)}>Preview output</button><button className="primary" onClick={() => void results.download(artifact)}>Download JSON</button><button className="secondary" onClick={() => selectTask(artifact.taskId)}>Open source task</button></div>{results.previews[artifact.id] && <PlanningOutput result={results.previews[artifact.id]!}/>}</article>)}</div>
  </div>
}
