import { useCallback, useRef, useState } from 'react'
import { ApiError, API_BASE, request } from '../api/client'
import type { ResultReviewItem, ReviewCommand, ReviewDecision, ReviewedArtifact, ReviewReceipt } from '../types/results'
import type { PlanningResult } from '../types/runtime'

const storageKey = 'jarvis.result-review.pending.v1'
function restored(): ReviewCommand | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null')
    if (!value || typeof value !== 'object') return null
    const item = value as Record<string, unknown>
    const fields = ['run_id', 'command_id', 'timestamp', 'actor_reference', 'execution_id', 'expected_result_hash', 'expected_checkpoint_id', 'expected_task_updated_at', 'decision', 'reason']
    if (item.command_type !== 'review_result' || fields.some(key => typeof item[key] !== 'string' || (item[key] as string).length > 2000) || !Number.isSafeInteger(item.expected_run_version) || !['accept', 'reject', 'request_changes'].includes(String(item.decision))) return null
    if ((item.decision === 'request_changes') !== (typeof item.revised_request === 'string')) return null
    return item as unknown as ReviewCommand
  } catch { return null }
}

export function useResultsState() {
  const [actorId, setActorId] = useState<string | null>(null)
  const [items, setItems] = useState<ResultReviewItem[]>([])
  const [artifacts, setArtifacts] = useState<ReviewedArtifact[]>([])
  const [previews, setPreviews] = useState<Record<string, PlanningResult>>({})
  const [pending, setPending] = useState(restored)
  const captured = useRef(pending)
  const [receipt, setReceipt] = useState<ReviewReceipt | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const inflight = useRef(false)
  const generation = useRef(0)

  const refresh = useCallback(async () => {
    const current = ++generation.current
    setLoading(true)
    setPreviews({})
    try {
      const operator = await request<{ actorId: string } | null>('/api/results/operator')
      if (current !== generation.current) return
      setActorId(operator?.actorId ?? null)
      if (!operator) { setItems([]); setArtifacts([]); return }
      const headers = { 'X-Jarvis-Actor-Id': operator.actorId }
      const [reviews, library] = await Promise.all([request<ResultReviewItem[]>('/api/results/reviews', { headers }), request<ReviewedArtifact[]>('/api/results/artifacts', { headers })])
      if (current === generation.current) { setItems(reviews); setArtifacts(library); setError('') }
    } catch (caught) {
      if (current === generation.current) { setItems([]); setArtifacts([]); setError(caught instanceof Error ? caught.message : 'Saved results are unavailable') }
    } finally { if (current === generation.current) setLoading(false) }
  }, [])

  const setup = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true; setBusy(true); setError('')
    try {
      await request('/api/results/operator/setup', { method: 'POST', body: JSON.stringify({ confirmation: 'enable_local_result_review' }) })
      await refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not enable local result review') }
    finally { inflight.current = false; setBusy(false) }
  }, [refresh])

  const send = useCallback(async (command: ReviewCommand) => {
    if (inflight.current) throw new Error('A review decision is already pending')
    inflight.current = true; setBusy(true); setError('')
    try {
      const result = await request<ReviewReceipt>(`/api/results/executions/${encodeURIComponent(command.execution_id)}/review`, { method: 'POST', headers: { 'X-Jarvis-Actor-Id': command.actor_reference }, body: JSON.stringify(command) })
      setReceipt(result)
      sessionStorage.removeItem(storageKey)
      captured.current = null; setPending(null)
      await refresh()
      return result
    } catch (caught) {
      if (caught instanceof ApiError && caught.status >= 400 && caught.status < 500 && caught.status !== 423) {
        sessionStorage.removeItem(storageKey); captured.current = null; setPending(null)
      }
      setError(caught instanceof Error ? caught.message : 'Decision acknowledgement was not confirmed')
      throw caught
    } finally { inflight.current = false; setBusy(false) }
  }, [refresh])

  const decide = useCallback(async (item: ResultReviewItem, decision: ReviewDecision, reason: string, revisedRequest?: string) => {
    if (captured.current || inflight.current) throw new Error('Resolve the captured decision before starting another review')
    if (!actorId || !item.execution.resultHash) throw new Error('Refresh reviewer and saved result before deciding')
    const command: ReviewCommand = { command_type: 'review_result', run_id: item.execution.runtimeRunId, command_id: `review-${crypto.randomUUID()}`, timestamp: new Date().toISOString(), actor_reference: actorId, expected_run_version: item.runVersion, execution_id: item.execution.executionId, expected_result_hash: item.execution.resultHash, expected_checkpoint_id: item.checkpointId, expected_task_updated_at: item.taskUpdatedAt, decision, reason, ...(decision === 'request_changes' ? { revised_request: revisedRequest } : {}) }
    try { sessionStorage.setItem(storageKey, JSON.stringify(command)) }
    catch { setError('Browser recovery storage is unavailable. The review decision was not sent.'); throw new Error('Review recovery storage is unavailable') }
    captured.current = command; setPending(command)
    return send(command)
  }, [actorId, send])

  const retry = useCallback(async () => {
    if (!captured.current) throw new Error('No captured decision to retry')
    return send(captured.current)
  }, [send])

  const artifactText = useCallback(async (artifact: ReviewedArtifact) => {
    if (!actorId) throw new Error('Enable local result review first')
    const response = await fetch(`${API_BASE}/api/results/artifacts/${encodeURIComponent(artifact.id)}/content`, { headers: { 'X-Jarvis-Actor-Id': actorId } })
    if (!response.ok) throw new Error('Result artifact could not be read with the current reviewer permission')
    const text = await response.text()
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
    const digest = [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('')
    if (digest !== artifact.resultHash) throw new Error('Result artifact hash did not match its saved evidence')
    return text
  }, [actorId])
  const preview = useCallback(async (artifact: ReviewedArtifact) => {
    try { const value = JSON.parse(await artifactText(artifact)) as PlanningResult; setPreviews(current => ({ ...current, [artifact.id]: value })); setError('') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Preview failed') }
  }, [artifactText])
  const download = useCallback(async (artifact: ReviewedArtifact) => {
    try {
      const text = await artifactText(artifact)
      const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
      const link = document.createElement('a'); link.href = url; link.download = `${artifact.id}.json`; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Download failed') }
  }, [artifactText])
  return { actorId, items, artifacts, previews, pending, receipt, busy, loading, error, refresh, setup, decide, retry, preview, download }
}
