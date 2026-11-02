import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import type { AuditEvent, Task } from '../types/contracts'
import type { RuntimeRun, RuntimePage, ModelExecution } from '../types/runtime'

interface Session { actorId: string; csrfToken: string; expiresInSeconds: number }
interface Status { emergencyStop: boolean; simulatorStatus: string; recoveryStatus: string;
  eventSessionId: string; lastSequenceNumber: number }
interface Page<T> { items: T[]; nextOffset: number | null }
interface RemoteAgent { id: string; display_name: string; lifecycle_state: string }
interface Detail { task: Task; graph: unknown; audit: Page<AuditEvent> }
interface WorkspaceIntent { repository_id: string; runtime_run_id: string; base_sha: string }
interface WorkspacePlan extends WorkspaceIntent { task_id: string; repository_identity: string;
  base_branch: string; branch: string; worktree_key: string; plan_hash: string; policy_digest: string }
interface WorkspaceApproval { approval_id: string; plan: WorkspacePlan; approved_by: string; expires_at: string }
interface RuntimeRequest { run_id: string; command_type: 'request_pause' | 'resume' | 'request_cancellation';
  command_id: string; expected_run_version: number; timestamp: string; detail: string;
  reason_code?: string; requester_reference?: string }

class RemoteError extends Error { constructor(public status: number, public code = "HTTP_ERROR") { super(
  code === "version_conflict" ? "Run changed. Refresh state before explicitly requesting a new runtime action." :
  status === 401 ? 'Session expired. Sign in again.' : status === 403
    ? 'Access denied. Check the operator permissions and browser session.'
    : 'Request failed. Refresh state before retrying a control.') } }

async function api<T>(path: string, init: RequestInit = {}, csrf?: string): Promise<T> {
  if (!path.startsWith('/api/remote/')) throw new Error('Dedicated remote API required')
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-Jarvis-CSRF', csrf)
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin',
    mode: 'same-origin', redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(10000) })
  if (!response.ok) {
    let code = 'HTTP_ERROR'
    try {
      const body = await response.json() as { error?: { code?: unknown } }
      if (typeof body.error?.code === 'string' && /^[A-Za-z0-9_.-]{1,80}$/.test(body.error.code)) code = body.error.code
    } catch { /* Unknown outcomes retain exact native command replay. */ }
    throw new RemoteError(response.status, code)
  }
  const body = await response.json() as { data: T }
  return body.data
}

function useOperatorState() {
  const [session, setSession] = useState<Session | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [goals, setGoals] = useState<Task[]>([])
  const [agents, setAgents] = useState<RemoteAgent[]>([])
  const [status, setStatus] = useState<Status | null>(null)
  const [runs, setRuns] = useState<RuntimePage | null>(null)
  const [pendingRuntime, setPendingRuntime] = useState<RuntimeRequest | null>(null)
  const [runtimeEvidence, setRuntimeEvidence] = useState<{ run: RuntimeRun; result: ModelExecution | null } | null>(null)
  const [workspaceReview, setWorkspaceReview] = useState<{ request: WorkspaceIntent; plan: WorkspacePlan; generation: number; order: number } | null>(null)
  const [workspaceApproval, setWorkspaceApproval] = useState<WorkspaceApproval | null>(null)
  const reviewOrder = useRef(0), inspectionOrder = useRef(0)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [authBusy, setAuthBusy] = useState(false)
  const authLocked = useRef(false), readOrder = useRef(0)
  const [error, setError] = useState<string | null>(null)
  const [lastSync, setLastSync] = useState<string | null>(null)
  const csrf = useRef<string | undefined>(undefined)
  const generation = useRef(0), selected = useRef<string | null>(null), page = useRef(0)
  const mutating = useRef(false)
  const pending = useRef<{ payload: string; key: string } | null>(null)

  const clear = useCallback(() => {
    generation.current += 1; readOrder.current += 1
    mutating.current = false; setBusy(false)
    csrf.current = undefined; selected.current = null
    pending.current = null; page.current = 0
    reviewOrder.current += 1; inspectionOrder.current += 1
    setRuns(null); setPendingRuntime(null); setWorkspaceReview(null); setWorkspaceApproval(null); setRuntimeEvidence(null)
    setSession(null); setGoals([]); setAgents([]); setStatus(null); setDetail(null)
    setSelectedId(null); setNextOffset(null); setOffset(0); setLastSync(null)
  }, [])
  const failure = useCallback((caught: unknown) => {
    if (caught instanceof RemoteError && caught.status === 401) clear()
    setError(caught instanceof RemoteError ? caught.message
      : 'Connection interrupted. Refresh and inspect recorded state before retrying.')
  }, [clear])

  const refresh = useCallback(async () => {
    if (!csrf.current) return
    const version = generation.current, token = csrf.current, currentPage = page.current
    const read = ++readOrder.current
    try {
      const [system, tasks, workforce, runtime] = await Promise.allSettled([
        api<Status>('/api/remote/status', {}, token),
        api<Page<Task>>('/api/remote/goals?offset=' + currentPage + '&limit=20', {}, token),
        api<Page<RemoteAgent>>('/api/remote/agents?limit=50', {}, token),
        api<RuntimePage>('/api/remote/runtime/runs?limit=20' + (selected.current ? '&task_id=' + encodeURIComponent(selected.current) : ''), {}, token),
      ])
      if (version !== generation.current || read !== readOrder.current || currentPage !== page.current) return
      const rejected = [system, tasks, workforce, runtime].filter(item => item.status === 'rejected')
      const expired = rejected.find(item => item.reason instanceof RemoteError && item.reason.status === 401)
      if (expired) { failure(expired.reason); return }
      setRuns(runtime.status === 'fulfilled' ? runtime.value : null)
      setStatus(system.status === 'fulfilled' ? system.value : null)
      setGoals(tasks.status === 'fulfilled' ? tasks.value.items : [])
      setNextOffset(tasks.status === 'fulfilled' ? tasks.value.nextOffset : null)
      setAgents(workforce.status === 'fulfilled' ? workforce.value.items : [])
      setLastSync(new Date().toISOString())
      if (rejected.length) failure(rejected[0]!.reason)
      else setError(null)
      if (tasks.status === 'rejected') { selected.current = null; setSelectedId(null); setDetail(null) }
      if (selected.current) {
        const id = selected.current, encoded = encodeURIComponent(id)
        const [task, graph, audit] = await Promise.all([
          api<Task>('/api/remote/goals/' + encoded, {}, token),
          api<unknown>('/api/remote/goals/' + encoded + '/graph', {}, token),
          api<Page<AuditEvent>>('/api/remote/goals/' + encoded + '/audit?limit=20', {}, token),
        ])
        if (version === generation.current && read === readOrder.current && selected.current === id) setDetail({ task, graph, audit })
      }
    } catch (caught) { if (version === generation.current && read === readOrder.current) failure(caught) }
  }, [failure])

  const establish = useCallback(async (summary: Session) => {
    generation.current += 1; csrf.current = summary.csrfToken
    setSession(summary); setError(null); await refresh()
  }, [refresh])
  useEffect(() => {
    let active = true
    const version = generation.current
    void api<Session>('/api/remote/session/restore', { method: 'POST',
      headers: { 'X-Jarvis-Session': 'restore' } }).then(summary => {
      if (active && version === generation.current) return establish(summary)
    }).catch(caught => {
      if (active && version === generation.current && !(caught instanceof RemoteError && caught.status === 401)) failure(caught)
    }).finally(() => { if (active) setRestoring(false) })
    return () => { active = false }
  }, [establish, failure])

  useEffect(() => {
    if (!session) return
    const expiry = window.setTimeout(() => { clear(); setError('Session expired. Sign in again.') },
      session.expiresInSeconds * 1000)
    return () => window.clearTimeout(expiry)
  }, [session, clear])
  useEffect(() => {
    if (!session || error) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && navigator.onLine && !mutating.current) void refresh()
    }, 15000)
    return () => window.clearInterval(timer)
  }, [session, error, refresh])

  const login = useCallback(async (credential: string) => {
    if (authLocked.current) throw new Error('Await the current sign-in or sign-out')
    authLocked.current = true; setAuthBusy(true); clear(); setError(null)
    const version = generation.current
    try {
      const summary = await api<Session>('/api/remote/session', { method: 'POST',
        headers: { Authorization: 'Bearer ' + credential } })
      if (version === generation.current) await establish(summary)
    } catch (caught) {
      if (version === generation.current) failure(caught)
      throw new Error('Sign-in failed', { cause: caught })
    } finally { authLocked.current = false; setAuthBusy(false) }
  }, [establish, failure, clear])
  const logout = useCallback(async () => {
    if (authLocked.current) throw new Error('Await the current sign-in or sign-out')
    authLocked.current = true; setAuthBusy(true)
    const version = generation.current, token = csrf.current
    try {
      await api<boolean>('/api/remote/session/logout', { method: 'POST' }, token)
      if (version === generation.current) clear()
    } catch (caught) {
      if (version === generation.current) failure(caught)
      throw new Error('Sign-out was not acknowledged', { cause: caught })
    } finally { authLocked.current = false; setAuthBusy(false) }
  }, [clear, failure])
  const selectGoal = useCallback(async (id: string) => {
    reviewOrder.current += 1; inspectionOrder.current += 1
    setWorkspaceReview(null); setWorkspaceApproval(null); setRuntimeEvidence(null)
    selected.current = id; setSelectedId(id); setDetail(null); await refresh()
  }, [refresh])
  const loadPage = useCallback(async (value: number) => {
    if (!Number.isInteger(value) || value < 0 || value > 100000) return
    page.current = value; setOffset(value); selected.current = null
    setSelectedId(null); setDetail(null); await refresh()
  }, [refresh])

  const submitGoal = useCallback(async (title: string, description: string) => {
    if (!csrf.current || mutating.current) throw new Error('Sign in and await the current control')
    const payload = JSON.stringify({ title, description, priority: 'medium' })
    if (!pending.current || pending.current.payload !== payload)
      pending.current = { payload, key: crypto.randomUUID() }
    const version = generation.current
    readOrder.current += 1; mutating.current = true; setBusy(true)
    try {
      const task = await api<Task>('/api/remote/goals', { method: 'POST', body: payload,
        headers: { 'Idempotency-Key': pending.current.key } }, csrf.current)
      if (version !== generation.current) return null
      pending.current = null; page.current = 0; setOffset(0)
      selected.current = task.id; setSelectedId(task.id); setDetail(null)
      await refresh()
      return version === generation.current ? task : null
    } catch (caught) {
      if (version !== generation.current) return null
      readOrder.current += 1
      failure(caught); throw new Error('Submission acknowledgement unavailable', { cause: caught })
    } finally { if (version === generation.current) { mutating.current = false; setBusy(false) } }
  }, [refresh, failure])
  const control = useCallback(async (operation: 'stop' | 'resume' | 'cancel', taskId?: string) => {
    if (!csrf.current || mutating.current || (error && operation !== 'stop'))
      throw new Error('Refresh recorded state before controlling work')
    const version = generation.current
    readOrder.current += 1; mutating.current = true; setBusy(true)
    try {
      const path = operation === 'cancel' ? '/api/remote/goals/' + encodeURIComponent(taskId ?? '') + '/cancel'
        : '/api/remote/system/' + (operation === 'stop' ? 'emergency-stop' : 'resume')
      await api(path, { method: 'POST' }, csrf.current)
      if (version === generation.current) await refresh()
    } catch (caught) {
      if (version !== generation.current) return
      readOrder.current += 1
      failure(caught); throw new Error('Control acknowledgement unavailable', { cause: caught })
    } finally { if (version === generation.current) { mutating.current = false; setBusy(false) } }
  }, [refresh, failure, error])

  const runtimeControl = useCallback(async (runId: string,
    operation: 'request_pause' | 'resume' | 'request_cancellation') => {
    if (!csrf.current || mutating.current || error || authLocked.current)
      throw new Error('Refresh recorded state before controlling a run')
    const run = runs?.items.find(item => item.specification.run_id === runId)
    if (!run) throw new Error('Refresh the current runtime run before controlling it')
    const command = pendingRuntime?.run_id === runId && pendingRuntime.command_type === operation
      ? pendingRuntime : { run_id: runId, command_type: operation,
        command_id: crypto.randomUUID(), expected_run_version: run.version,
        timestamp: new Date().toISOString(), detail: 'Requested by authenticated browser operator',
        ...(operation === 'resume' ? {} : { reason_code: 'operator_request' }),
        ...(operation === 'request_cancellation' ? { requester_reference: session!.actorId } : {}) }
    setPendingRuntime(command)
    const version = generation.current
    readOrder.current += 1; mutating.current = true; setBusy(true)
    try {
      await api('/api/remote/runtime/commands', { method: 'POST',
        body: JSON.stringify(command) }, csrf.current)
      if (version !== generation.current) return
      setPendingRuntime(null); await refresh()
    } catch (caught) {
      if (version !== generation.current) return
      if (caught instanceof RemoteError && caught.status === 409 && caught.code === "version_conflict")
        setPendingRuntime(null)
      readOrder.current += 1; failure(caught)
      throw new Error('Runtime control acknowledgement unavailable', { cause: caught })
    } finally { if (version === generation.current) { mutating.current = false; setBusy(false) } }
  }, [runs, pendingRuntime, session, error, refresh, failure])

  const inspectRun = useCallback(async (runId: string) => {
    if (!csrf.current) return
    const version = generation.current, order = ++inspectionOrder.current
    setRuntimeEvidence(null)
    try {
      const id = encodeURIComponent(runId)
      const [run, result] = await Promise.all([
        api<RuntimeRun>('/api/remote/runtime/runs/' + id, {}, csrf.current),
        api<ModelExecution | null>('/api/remote/runtime/runs/' + id + '/result', {}, csrf.current),
      ])
      if (version === generation.current && order === inspectionOrder.current)
        setRuntimeEvidence({ run, result })
    } catch (caught) {
      if (version === generation.current && order === inspectionOrder.current) failure(caught)
    }
  }, [failure])

  const previewWorkspace = useCallback(async (repositoryId: string, runId: string, baseSha: string) => {
    if (!csrf.current || mutating.current || authLocked.current)
      throw new Error('Sign in and await the current control')
    const request = { repository_id: repositoryId, runtime_run_id: runId, base_sha: baseSha }
    const version = generation.current, order = ++reviewOrder.current
    setWorkspaceReview(null); setWorkspaceApproval(null)
    try {
      const plan = await api<WorkspacePlan>('/api/remote/self-build/workspaces/preview',
        { method: 'POST', body: JSON.stringify(request) }, csrf.current)
      if (plan.repository_id !== repositoryId || plan.runtime_run_id !== runId
        || plan.base_sha !== baseSha || !/^[a-f0-9]{64}$/.test(plan.plan_hash))
        throw new Error('Workspace plan differs from the requested scope')
      if (version === generation.current && order === reviewOrder.current)
        setWorkspaceReview({ request, plan, generation: version, order })
    } catch (caught) {
      if (version === generation.current && order === reviewOrder.current) failure(caught)
      throw new Error('Workspace preview unavailable', { cause: caught })
    }
  }, [failure])

  const approveWorkspace = useCallback(async (confirmedHash: string) => {
    const review = workspaceReview
    if (!csrf.current || mutating.current || authLocked.current || error || !review
      || review.generation !== generation.current || review.order !== reviewOrder.current
      || confirmedHash !== review.plan.plan_hash)
      throw new Error('Review and confirm the exact native workspace plan hash')
    const version = generation.current, order = reviewOrder.current
    readOrder.current += 1; mutating.current = true; setBusy(true)
    try {
      const approval = await api<WorkspaceApproval>('/api/remote/self-build/workspaces/approve',
        { method: 'POST', body: JSON.stringify({ ...review.request,
          expected_plan_hash: review.plan.plan_hash, valid_for_seconds: 900 }) }, csrf.current)
      if (version === generation.current && order === reviewOrder.current) {
        setWorkspaceApproval(approval); await refresh()
      }
    } catch (caught) {
      if (version !== generation.current) return
      readOrder.current += 1
      if (order === reviewOrder.current) setWorkspaceReview(null)
      failure(caught); throw new Error('Approval outcome needs audit inspection and fresh preview', { cause: caught })
    } finally { if (version === generation.current) { mutating.current = false; setBusy(false) } }
  }, [workspaceReview, error, refresh, failure])


  return { session, restoring, goals, agents, status, detail, selectedId, nextOffset, offset,
    busy, authBusy, error, lastSync, login, logout, refresh, selectGoal, loadPage, submitGoal, control,
    runs, runtimeControl, pendingRuntime, inspectRun, runtimeEvidence,
    previewWorkspace, approveWorkspace, workspaceReview, workspaceApproval }
}

const Context = createContext<ReturnType<typeof useOperatorState> | null>(null)
export function RemoteOperatorProvider({ children }: { children: ReactNode }) {
  const value = useOperatorState()
  return <Context.Provider value={value}>{children}</Context.Provider>
}
export function useRemoteOperator() {
  const value = useContext(Context)
  if (!value) throw new Error('RemoteOperatorProvider is required')
  return value
}
