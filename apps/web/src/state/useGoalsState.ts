import { useCallback, useRef, useState } from 'react'
import { ApiError, request } from '../api/client'
import type { Goal } from '../types/goals'
import type { PlanningConsent } from '../types/goalPlanning'
import { useGoalPlanningState } from './useGoalPlanningState'

type Pending = { key: string; request: string; planningConsent?: PlanningConsent; actorId?: string; invalidScope?: boolean }
const storageKey = 'jarvis.goal.pending.v1'
function restorePending(): Pending | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null')
    if (value && typeof value === 'object' && 'key' in value && 'request' in value && typeof value.key === 'string' && typeof value.request === 'string' && value.key.length <= 120 && value.request.length <= 2000) {
      const base: Pending = { key: value.key, request: value.request }
      if ('planningConsent' in value || 'actorId' in value) {
        const scope = 'planningConsent' in value ? value.planningConsent : null
        if (scope && typeof scope === 'object' && 'policyId' in scope && typeof scope.policyId === 'string' && scope.policyId.length <= 80 && 'policyHash' in scope && typeof scope.policyHash === 'string' && /^[a-f0-9]{64}$/.test(scope.policyHash) && 'actorId' in value && typeof value.actorId === 'string' && value.actorId.length <= 80) return { ...base, planningConsent: { policyId: scope.policyId, policyHash: scope.policyHash }, actorId: value.actorId }
        return { ...base, invalidScope: true }
      }
      return base
    }
  } catch { /* An invalid session draft cannot authorize a request. */ }
  return null
}

export function useGoalsState() {
  const planning = useGoalPlanningState()
  const [items, setItems] = useState<Goal[]>([])
  const [selected, setSelected] = useState<Goal | null>(null)
  const [pending, setPending] = useState(restorePending)
  const pendingRef = useRef(pending)
  const busyRef = useRef(false)
  const generation = useRef(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const historyGeneration = useRef(0)
  const refresh = useCallback(async () => {
    const current = ++historyGeneration.current
    setLoading(true)
    try {
      const goals = await request<Goal[]>('/api/goals?limit=30')
      if (current === historyGeneration.current) { setItems(goals); setHistoryError('') }
    } catch (caught) {
      if (current === historyGeneration.current) setHistoryError(caught instanceof Error ? caught.message : 'Could not load recent conversations')
    } finally { if (current === historyGeneration.current) setLoading(false) }
  }, [])
  const open = useCallback(async (id: string) => {
    const current = ++generation.current
    try {
      const goal = await request<Goal>(`/api/goals/${encodeURIComponent(id)}`)
      if (current === generation.current) { setSelected(goal); setError('') }
    } catch (caught) { if (current === generation.current) setError(caught instanceof Error ? caught.message : 'Could not open conversation') }
  }, [])
  const submit = useCallback(async (text: string, plan = false) => {
    if (busyRef.current) throw new Error('A submission is already pending')
    if (Array.from(text.trim()).length < 3 || Array.from(text).length > 2000) {
      const failure = new Error('Describe your goal using at least three and at most 2,000 characters.')
      setError(failure.message)
      throw failure
    }
    if (pendingRef.current && pendingRef.current.request !== text) throw new Error('Resolve the pending submission before changing its request')
    if (pendingRef.current?.invalidScope) { const failure = new Error('The captured planning scope is damaged. Reconcile saved history before submitting another request.'); setError(failure.message); throw failure }
    let submission = pendingRef.current
    if (!submission) {
      submission = { key: crypto.randomUUID(), request: text }
      if (plan) {
        const policy = planning.policy
        if (!planning.actorId || !policy?.enabled || !policy.id || !policy.policyHash || policy.unavailableReason || !policy.expiresAt || Date.parse(policy.expiresAt) <= Date.now() || policy.admittedGoals >= policy.maximumGoals) { const failure = new Error('Refresh or renew bounded planning consent before sending this goal.'); setError(failure.message); throw failure }
        submission = { ...submission, actorId: planning.actorId, planningConsent: { policyId: policy.id, policyHash: policy.policyHash } }
      }
    }
    // Persist before dispatch. If storage is unavailable, fail before making work.
    try { sessionStorage.setItem(storageKey, JSON.stringify(submission)) }
    catch {
      const failure = new Error('Browser storage is unavailable. Enable session storage so Jarvis can retain a safe retry key before saving work.')
      setError(failure.message)
      throw failure
    }
    pendingRef.current = submission
    setPending(submission)
    busyRef.current = true
    setBusy(true)
    setError('')
    const selectionGeneration = ++generation.current
    ++historyGeneration.current
    try {
      const goal = await request<Goal>('/api/goals', { method: 'POST', headers: { 'Idempotency-Key': submission.key, ...(submission.actorId ? { 'X-Jarvis-Actor-Id': submission.actorId } : {}) }, body: JSON.stringify({ request: submission.request, ...(submission.planningConsent ? { planningConsent: submission.planningConsent } : {}) }) })
      ++historyGeneration.current
      sessionStorage.removeItem(storageKey)
      pendingRef.current = null
      setPending(null)
      if (selectionGeneration === generation.current) setSelected(goal)
      setItems(current => [goal, ...current.filter(item => item.id !== goal.id)])
      return goal
    } catch (caught) {
      // HTTP request validation runs before the native transaction; it is a definite rejection.
      // All uncertain outcomes retain the exact request/key for safe replay.
      if (caught instanceof ApiError && (caught.status === 422 || (caught.status === 409 && caught.code.startsWith('GOAL_PLANNING_')))) {
        sessionStorage.removeItem(storageKey)
        pendingRef.current = null
        setPending(null)
      }
      setError(caught instanceof Error ? caught.message : 'Submission could not be confirmed')
      throw caught
    } finally { busyRef.current = false; setBusy(false); setLoading(false) }
  }, [planning.actorId, planning.policy])
  const newConversation = useCallback(() => { ++generation.current; setSelected(null); setError('') }, [])
  return { items, selected, pending, busy, loading, error, historyError, refresh, open, submit, newConversation, planning }
}
