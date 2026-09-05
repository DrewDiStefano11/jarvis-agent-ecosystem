import { useCallback, useEffect, useRef, useState } from 'react'
import { request } from '../api/client'
import type { ModelExecution, RuntimeIdentity, RuntimePage, RuntimeRun } from '../types/runtime'

/** Shared runtime projection; reuses AppStore synchronization, never another socket. */
export function useRuntimeState(lastSync: string | null) {
  const [actorId, setActor] = useState('')
  const [identities, setIdentities] = useState<RuntimeIdentity[]>([])
  const [runs, setRuns] = useState<RuntimeRun[]>([])
  const [executions, setExecutions] = useState<ModelExecution[]>([])
  const [taskId, setTask] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const generation = useRef(0)
  const actorRef = useRef(actorId)

  const loadIdentities = useCallback(async () => {
    const data = await request<RuntimeIdentity[]>('/api/identity/agents?limit=100')
    setIdentities(data)
  }, [])

  const selectActor = useCallback((id: string) => {
    generation.current += 1
    actorRef.current = id
    setActor(id)
    setRuns([])
    setExecutions([])
    setError(null)
    setNextOffset(null)
    setLoading(false)
  }, [])

  const setTaskId = useCallback((id: string) => {
    generation.current += 1
    setTask(id)
    setNextOffset(null)
    setLoading(false)
    setRuns([])
    setExecutions([])
    setError(null)
  }, [])

  const refreshRuntime = useCallback(async () => {
    if (!actorId) return
    const current = ++generation.current
    setLoading(true)
    try {
      const headers = { 'X-Jarvis-Actor-Id': actorId }
      const [page, results] = await Promise.all([
        request<RuntimePage>(`/api/agent-runtime/runs?limit=50${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, { headers }),
        taskId ? request<ModelExecution[]>(`/api/model-executions?taskId=${encodeURIComponent(taskId)}`, { headers }) : Promise.resolve([]),
      ])
      if (current !== generation.current) return
      setRuns(page.items)
      setExecutions(results)
      setNextOffset(page.next_offset)
      setError(null)
    } catch (caught) {
      if (current !== generation.current) return
      // Authorization revocation must clear previously disclosed result text.
      setRuns([])
      setExecutions([])
      setError(caught instanceof Error ? caught.message : 'Runtime synchronization failed')
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [actorId, taskId])

  useEffect(() => {
    let cancelled = false
    // Coalesce synchronization into the microtask queue; obsolete selections
    // never start a request, and cleanup invalidates already-running responses.
    void Promise.resolve().then(() => { if (!cancelled) void refreshRuntime() })
    return () => { cancelled = true; generation.current += 1 }
  }, [refreshRuntime, lastSync])
  useEffect(() => () => { generation.current += 1 }, [])

  const command = useCallback(async (body: unknown) => {
    const selectedActor = actorRef.current
    if (!selectedActor) throw new Error('Select an active local identity first.')
    const result = await request<{ snapshot: RuntimeRun }>('/api/agent-runtime/commands', {
      method: 'POST', headers: { 'X-Jarvis-Actor-Id': selectedActor }, body: JSON.stringify(body),
    })
    await refreshRuntime()
    return result.snapshot
  }, [refreshRuntime])

  return { actorId, selectActor, identities, loadIdentities, runs, executions, taskId, setTaskId,
    error, loading, nextOffset, refreshRuntime, command }
}
