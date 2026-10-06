import { useCallback, useEffect, useRef, useState } from 'react'
import { request } from '../api/client'
import { identityPages, registerIdentity } from '../api/identities'
import type { IdentityCapability, IdentityRegistration, ModelExecution, RuntimeIdentity, RuntimePage, RuntimeRun } from '../types/runtime'

export const RUNTIME_HISTORY_PAGE_LIMIT = 4
export const RUNTIME_HISTORY_PAGE_SIZE = 50

/** Shared runtime projection; reuses AppStore synchronization, never another socket. */
export function useRuntimeState(lastSync: string | null) {
  const [actorId, setActor] = useState('')
  const [identities, setIdentities] = useState<RuntimeIdentity[]>([])
  const [identityError, setIdentityError] = useState<string | null>(null)
  const [identityLoading, setIdentityLoading] = useState(false)
  const [capabilities, setCapabilities] = useState<IdentityCapability[]>([])
  const [capabilityMembers, setCapabilityMembers] = useState<Record<string, string[]>>({})
  const identityGeneration = useRef(0)
  const [runs, setRuns] = useState<RuntimeRun[]>([])
  const [executions, setExecutions] = useState<ModelExecution[]>([])
  const [taskId, setTask] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [pagesLoaded, setPagesLoaded] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const requestTicket = useRef<number | null>(null)
  const generation = useRef(0)
  const actorRef = useRef(actorId)

  const loadIdentities = useCallback(async () => {
    const current = ++identityGeneration.current
    setIdentityLoading(true)
    try {
      const data = await identityPages<RuntimeIdentity>('/api/identity/agents')
      if (current === identityGeneration.current) { setIdentities(data); setIdentityError(null) }
    } catch (caught) {
      if (current === identityGeneration.current) setIdentityError(caught instanceof Error ? caught.message : 'Cannot load identities')
      throw caught
    } finally { if (current === identityGeneration.current) setIdentityLoading(false) }
  }, [])

  const saveIdentity = useCallback((identity: RuntimeIdentity) => {
    identityGeneration.current += 1
    setIdentityLoading(false)
    setIdentities(current => [...current.filter(item => item.id !== identity.id), identity].sort((a, b) => a.stable_key.localeCompare(b.stable_key)))
  }, [])
  const createIdentity = useCallback(async (body: IdentityRegistration) => {
    const result = await registerIdentity(body)
    saveIdentity(result.identity)
    return result
  }, [saveIdentity])
  const updateIdentity = useCallback(async (id: string, body: { display_name?: string; description?: string; is_enabled?: boolean }) => {
    const identity = await request<RuntimeIdentity>(`/api/identity/agents/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) })
    saveIdentity(identity)
    return identity
  }, [saveIdentity])
  const transitionIdentity = useCallback(async (id: string, transition: 'activate' | 'suspend') => {
    const identity = await request<RuntimeIdentity>(`/api/identity/agents/${encodeURIComponent(id)}/${transition}`, { method: 'POST' })
    saveIdentity(identity)
    return identity
  }, [saveIdentity])
  const loadCapabilities = useCallback(async () => {
    setCapabilities(await identityPages<IdentityCapability>('/api/identity/capabilities'))
  }, [])
  const loadCapabilityMembers = useCallback(async (key: string) => {
    const matches = await identityPages<RuntimeIdentity>(`/api/identity/agents?capability=${encodeURIComponent(key)}`)
    setCapabilityMembers(current => ({ ...current, [key]: matches.map(identity => identity.id) }))
  }, [])

  const selectActor = useCallback((id: string) => {
    generation.current += 1
    requestTicket.current = null
    setPagesLoaded(0)
    setLoadingMore(false)
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
    requestTicket.current = null
    setPagesLoaded(0)
    setLoadingMore(false)
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
    requestTicket.current = current
    setLoadingMore(false)
    setLoading(true)
    try {
      const headers = { 'X-Jarvis-Actor-Id': actorId }
      const [page, results] = await Promise.all([
        request<RuntimePage>(`/api/agent-runtime/runs?limit=50${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, { headers }),
        taskId ? request<ModelExecution[]>(`/api/model-executions?taskId=${encodeURIComponent(taskId)}`, { headers }) : Promise.resolve([]),
      ])
      if (current !== generation.current) return
      setRuns(page.items)
      setPagesLoaded(1)
      setExecutions(results)
      setNextOffset(page.next_offset)
      setError(null)
    } catch (caught) {
      if (current !== generation.current) return
      // Authorization revocation must clear previously disclosed result text.
      setRuns([])
      setExecutions([])
      setNextOffset(null)
      setPagesLoaded(0)
      setError(caught instanceof Error ? caught.message : 'Runtime synchronization failed')
    } finally {
      if (current === generation.current) { requestTicket.current = null; setLoading(false) }
    }
  }, [actorId, taskId])

  const loadMoreRuns = useCallback(async () => {
    if (!actorId || nextOffset === null || pagesLoaded >= RUNTIME_HISTORY_PAGE_LIMIT || requestTicket.current !== null) return
    const offset = nextOffset
    const current = ++generation.current
    requestTicket.current = current
    setLoading(true)
    setLoadingMore(true)
    try {
      const page = await request<RuntimePage>(`/api/agent-runtime/runs?limit=${RUNTIME_HISTORY_PAGE_SIZE}&offset=${offset}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ''}`, {
        headers: { 'X-Jarvis-Actor-Id': actorId },
      })
      if (current !== generation.current) return
      if (page.next_offset !== null && page.next_offset <= offset) throw new Error('Runtime history did not advance. Refresh to read it again.')
      setRuns(previous => {
        const merged = new Map(previous.map(run => [run.specification.run_id, run]))
        for (const run of page.items) {
          const existing = merged.get(run.specification.run_id)
          if (!existing || run.version >= existing.version) merged.set(run.specification.run_id, run)
        }
        return [...merged.values()].slice(0, RUNTIME_HISTORY_PAGE_LIMIT * RUNTIME_HISTORY_PAGE_SIZE)
      })
      setPagesLoaded(previous => previous + 1)
      setNextOffset(page.next_offset)
      setError(null)
    } catch (caught) {
      if (current !== generation.current) return
      // A failed authorized read may represent revocation: clear all disclosures.
      setRuns([])
      setExecutions([])
      setNextOffset(null)
      setPagesLoaded(0)
      setError(caught instanceof Error ? caught.message : 'Runtime history could not be loaded')
    } finally {
      if (current === generation.current) {
        requestTicket.current = null
        setLoading(false)
        setLoadingMore(false)
      }
    }
  }, [actorId, taskId, nextOffset, pagesLoaded])

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

  return { actorId, selectActor, identities, loadIdentities, identityError, identityLoading, createIdentity,
    updateIdentity, transitionIdentity, capabilities, capabilityMembers, loadCapabilities, loadCapabilityMembers,
    runs, executions, taskId, setTaskId,
    error, loading, loadingMore, pagesLoaded, nextOffset, loadMoreRuns, refreshRuntime, command }
}
