import type { PlanningResult } from '../types/runtime'

export function PlanningOutput({ result }: { result: PlanningResult }) {
  return <div className="planning-output">
    <p className="result-summary">{result.summary}</p>
    <section><h3>Analysis</h3><p>{result.analysis}</p></section>
    <section><h3>Recommendations <span>{result.recommendations.length}</span></h3><ol className="result-recommendations">{result.recommendations.map((item, index) => <li key={index}><div><h4>{item.title}</h4><span>{item.priority} priority</span></div><p>{item.description}</p></li>)}</ol></section>
    <section><h3>Risks</h3>{result.risks.length ? result.risks.map((risk, index) => <article key={index}><h4>{risk.title} · {risk.severity}</h4><p>{risk.description}</p><p><strong>Mitigation:</strong> {risk.mitigation}</p></article>) : <p>No risks were reported by this result.</p>}</section>
    <div className="result-evidence"><section><h3>Assumptions</h3>{result.assumptions.length ? <ul>{result.assumptions.map((text, index) => <li key={index}>{text}</li>)}</ul> : <p>No assumptions were reported.</p>}</section><section><h3>Missing information</h3>{result.missingInformation.length ? <ul>{result.missingInformation.map((text, index) => <li key={index}>{text}</li>)}</ul> : <p>No missing information was reported.</p>}</section></div>
  </div>
}
