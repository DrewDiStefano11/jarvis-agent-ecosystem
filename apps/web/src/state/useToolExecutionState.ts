import { useCallback, useEffect, useRef, useState } from 'react'
import { request } from '../api/client'
import type { ToolArtifactContent, ToolExecution, ToolScope, WorkspaceInfo } from '../types/toolExecution'

/** The same approved plan and scope keep one command identity across reloads. */
export async function toolAuthorizationBody(actorId: string, sourceExecutionId: string, expectedPlanHash: string, scope: ToolScope) {
  const payload = { sourceExecutionId, expectedPlanHash, scope }
  const bytes = new TextEncoder().encode(JSON.stringify([actorId, payload]))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  const key = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
  return { commandId: `tool-authorize-${key}`, ...payload }
}

/** Canonical tool projection, refreshed by the existing AppStore event stream. */
export function useToolExecutionState(actorId: string, taskId: string, lastSync: string | null) {
  const [state, setState] = useState<{ key: string; workspaces: WorkspaceInfo[]; executions: ToolExecution[]; artifact: ToolArtifactContent | null; error: string | null; loading: boolean }>({ key: '', workspaces: [], executions: [], artifact: null, error: null, loading: false })
  const key = `${actorId}:${taskId}`
  const generation = useRef(0)
  const refreshTools = useCallback(async () => {
    if (!actorId || !taskId) return
    const current = ++generation.current
    setState(previous => ({ ...previous, loading: true }))
    try {
      const headers = { 'X-Jarvis-Actor-Id': actorId }
      const [workspaces, executions] = await Promise.all([
        request<WorkspaceInfo[]>('/api/tool-workspaces', { headers }),
        request<ToolExecution[]>(`/api/tool-executions?taskId=${encodeURIComponent(taskId)}`, { headers }),
      ])
      if (current !== generation.current) return
      setState(previous => ({ key, workspaces, executions, artifact: previous.key === key ? previous.artifact : null, error: null, loading: false }))
    } catch (caught) {
      if (current !== generation.current) return
      setState({ key, workspaces: [], executions: [], artifact: null, error: caught instanceof Error ? caught.message : 'Cannot load authorized tools', loading: false })
    }
  }, [actorId, taskId, key])
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => { if (!cancelled) void refreshTools() })
    return () => { cancelled = true; generation.current += 1 }
  }, [refreshTools, lastSync])
  const authorize = useCallback(async (sourceExecutionId: string, planHash: string, scope: ToolScope) => {
    if (!actorId) throw new Error('Select an authorized local identity first.')
    const body = await toolAuthorizationBody(actorId, sourceExecutionId, planHash, scope)
    const result = await request<ToolExecution>('/api/tool-executions/authorize', {
      method: 'POST', headers: { 'X-Jarvis-Actor-Id': actorId }, body: JSON.stringify(body),
    })
    await refreshTools()
    return result
  }, [actorId, refreshTools])
  const openArtifact = useCallback(async (artifactId: string) => {
    if (!actorId) return
    const current = generation.current
    try {
      const artifact = await request<ToolArtifactContent>(`/api/tool-artifacts/${encodeURIComponent(artifactId)}`, { headers: { 'X-Jarvis-Actor-Id': actorId } })
      if (current === generation.current) setState(previous => ({ ...previous, key, artifact, error: null }))
    } catch (caught) {
      if (current === generation.current) setState(previous => ({ ...previous, artifact: null, error: caught instanceof Error ? caught.message : 'Cannot load artifact' }))
    }
  }, [actorId, key])
  const visible = state.key === key && actorId ? state : { workspaces: [], executions: [], artifact: null, error: null, loading: false }
  return { ...visible, refreshTools, authorize, openArtifact }
}
