import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, request } from '../api/client'
import type { GoalPlanningPolicy, GoalPlanningAdmission } from '../types/goalPlanning'

type Change = { key: string; actorId: string; body: { confirmation: 'allow_bounded_goal_planning'; expectedRevision: number; maximumGoals: number; expiresAt: string; profile: 'direct-v1' } }
const key = 'jarvis.goal.policy.pending.v1'
function restore(): Change | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? 'null') as Change | null
    if (value && typeof value.key === 'string' && typeof value.actorId === 'string' && value.body?.confirmation === 'allow_bounded_goal_planning' && value.body.profile === 'direct-v1' && Number.isInteger(value.body.maximumGoals) && typeof value.body.expiresAt === 'string') return value
  } catch { /* Invalid local configuration cannot grant server authority. */ }
  return null
}
export function useGoalPlanningState() {
  const [policy, setPolicy] = useState<GoalPlanningPolicy | null>(null)
  const [actorId, setActorId] = useState<string | null>(null)
  const [pending, setPending] = useState(restore)
  const captured = useRef(pending)
  const [admissions, setAdmissions] = useState<Record<string, GoalPlanningAdmission>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const inflight = useRef(false)
  const generation = useRef(0)
  useEffect(() => {
    if (!policy?.enabled || !policy.expiresAt) return
    const policyId = policy.id
    const timer = window.setTimeout(() => setPolicy(current => current?.id === policyId ? { ...current, enabled: false } : current), Math.max(0, Math.min(Date.parse(policy.expiresAt) - Date.now(), 2147483647)))
    return () => window.clearTimeout(timer)
  }, [policy?.id, policy?.enabled, policy?.expiresAt])
  const refresh = useCallback(async () => {
    const current = ++generation.current
    try {
      const [operator, view] = await Promise.all([request<{ actorId: string } | null>('/api/goal-planning/operator'), request<GoalPlanningPolicy>('/api/goal-planning/policy')])
      if (current === generation.current) { setActorId(operator?.actorId ?? null); setPolicy(view); setError('') }
    } catch (caught) { if (current === generation.current) setError(caught instanceof Error ? caught.message : 'Planning configuration is unavailable') }
  }, [])
  const configure = useCallback(async (maximumGoals: number) => {
    if (inflight.current) return
    inflight.current = true; setBusy(true); setError('')
    try {
      let change = captured.current
      if (!change) {
        if (!policy || policy.unavailableReason) throw new Error('An eligible provider and explicitly configured planning worker are required first.')
        const operator = actorId ? { actorId } : await request<{ actorId: string }>('/api/goal-planning/operator/setup', { method: 'POST', body: JSON.stringify({ confirmation: 'configure_bounded_goal_planning' }) })
        change = { key: crypto.randomUUID(), actorId: operator.actorId, body: { confirmation: 'allow_bounded_goal_planning', expectedRevision: policy.revision, maximumGoals, expiresAt: new Date(Date.now() + 3600000).toISOString(), profile: 'direct-v1' } }
        try { sessionStorage.setItem(key, JSON.stringify(change)) } catch { throw new Error('Browser recovery storage is unavailable. Planning consent was not sent.') }
        captured.current = change; setPending(change)
      }
      await request('/api/goal-planning/policy', { method: 'POST', headers: { 'Idempotency-Key': change.key, 'X-Jarvis-Actor-Id': change.actorId }, body: JSON.stringify(change.body) })
      sessionStorage.removeItem(key); captured.current = null; setPending(null)
      await refresh()
    } catch (caught) {
      if (caught instanceof ApiError && [403, 409, 422].includes(caught.status)) { sessionStorage.removeItem(key); captured.current = null; setPending(null) }
      setError(caught instanceof Error ? caught.message : 'Planning consent acknowledgement was not confirmed')
    } finally { inflight.current = false; setBusy(false) }
  }, [actorId, policy, refresh])
  const revoke = useCallback(async () => {
    if (!actorId || !policy?.id || inflight.current || captured.current) return
    inflight.current = true; setBusy(true); setError('')
    setPolicy(current => current ? { ...current, enabled: false } : current)
    try {
      await request(`/api/goal-planning/policy/${encodeURIComponent(policy.id)}/revoke`, { method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify({ expectedPolicyHash: policy.policyHash }) })
      await refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Revocation could not be confirmed. Refresh before planning more work.') }
    finally { inflight.current = false; setBusy(false) }
  }, [actorId, policy, refresh])
  const inspect = useCallback(async (goalId: string) => {
    if (!actorId) return
    try {
      const result = await request<GoalPlanningAdmission>(`/api/goal-planning/goals/${encodeURIComponent(goalId)}`, { headers: { 'X-Jarvis-Actor-Id': actorId } })
      setAdmissions(current => ({ ...current, [goalId]: result }))
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not inspect planning admission') }
  }, [actorId])
  const retry = useCallback(async (goalId: string) => {
    if (!actorId || inflight.current) return
    inflight.current = true; setBusy(true)
    try {
      const view = await request<GoalPlanningAdmission>(`/api/goal-planning/goals/${encodeURIComponent(goalId)}/retry`, { method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId } })
      setAdmissions(current => ({ ...current, [goalId]: view }))
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Planning retry was not confirmed') }
    finally { inflight.current = false; setBusy(false) }
  }, [actorId])
  return { policy, actorId, pending, busy, error, admissions, refresh, configure, revoke, inspect, retry }
}
