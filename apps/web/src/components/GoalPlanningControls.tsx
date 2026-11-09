import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAppStore } from '../state/AppStore'

export function GoalPlanningControls() {
  const { goals } = useAppStore()
  const { planning } = goals
  const [maximum, setMaximum] = useState(5)
  const policy = planning.policy
  const active = policy?.enabled
  return <details className="goal-planning-controls"><summary>Bounded planning <span>{active ? 'Enabled for new goals' : 'Off'}</span></summary>
    <p>Each explicitly sent goal allows one planning request and one attempt, with a 7,500-token total bound including input, schema and up to 1,536 output tokens. No organizational calls, repairs, workspace tools or hidden retries.</p>
    <p>Consent expires in one hour. Every generated output awaits your review. This applies to new goals only and does not enable a provider or start a worker.</p>
    {policy?.unavailableReason && <p role="status">Planning is unavailable: {policy.unavailableReason.replaceAll('_', ' ')}. <Link to="/models">Inspect provider configuration →</Link></p>}
    {active && <p>{policy.admittedGoals} of {policy.maximumGoals} goal permissions used. Expires {new Date(policy.expiresAt!).toLocaleTimeString()}.</p>}
    <label>Maximum new goals<input type="number" min={1} max={20} value={planning.pending?.body.maximumGoals ?? maximum} disabled={planning.busy || Boolean(planning.pending)} onChange={event => setMaximum(Math.min(20, Math.max(1, Number(event.target.value))))}/></label>
    <div className="result-actions"><button className="secondary" disabled={planning.busy || (!planning.pending && (!policy || Boolean(policy.unavailableReason)))} onClick={() => void planning.configure(maximum)}>{planning.busy ? 'Saving consent…' : planning.pending ? 'Retry captured consent' : active ? 'Renew bounded planning consent' : 'Allow bounded planning'}</button>{active && <button className="secondary" disabled={planning.busy || Boolean(planning.pending)} onClick={() => void planning.revoke()}>Revoke planning consent</button>}<button className="secondary" disabled={planning.busy} onClick={() => void planning.refresh()}>Refresh planning settings</button></div>
    {planning.pending && <p role="status">A consent acknowledgement is unresolved. Retry uses its exact original limits and key.</p>}
    {planning.error && <p role="alert">{planning.error}</p>}
  </details>
}
