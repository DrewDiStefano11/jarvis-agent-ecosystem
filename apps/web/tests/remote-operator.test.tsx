import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { RemoteOperatorProvider, useRemoteOperator } from '../src/state/RemoteOperatorStore'
import { RemoteOperation } from '../src/pages/RemoteOperation'

afterEach(() => vi.unstubAllGlobals())
const session = { actorId: 'operator', csrfToken: 'c'.repeat(43), expiresInSeconds: 900 }
const reply = (data: unknown, status = 200) => ({ ok: status < 400, status,
  json: async () => status < 400 ? { data } : { error: { code: 'DENIED', message: 'Denied' } } })

function transport() {
  const calls: { path: string; init?: RequestInit }[] = []
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input); calls.push({ path, init })
    if (path.endsWith('/session/restore')) return reply(null, 401)
    if (path.endsWith('/session/logout')) return reply(true)
    if (path.endsWith('/session')) return reply(session)
    if (path.endsWith('/status')) return reply({ emergencyStop: false, simulatorStatus: 'idle',
      recoveryStatus: 'none', eventSessionId: 'event-1', lastSequenceNumber: 1 })
    if (path.includes('/goals') && init?.method === 'POST') return reply({
      id: 'task-1', title: 'Useful goal', status: 'queued', createdBy: 'operator' }, 201)
    return reply({ items: [], nextOffset: null })
  })
  vi.stubGlobal('fetch', fetcher)
  return { calls, fetcher }
}

it('labels seeded completed records separately from submitted goal evidence', async () => {
  const { fetcher } = transport()
  const original = fetcher.getMockImplementation()!
  const seeded = { id: 'task-completed', title: 'Example completed report', status: 'completed',
    progress: 100, assignedAgentIds: [], result: 'Example report', statusMessage: 'Complete' }
  const submitted = { ...seeded, id: 'task-real', title: 'Operator completed report' }
  fetcher.mockImplementation(async (input, init) => {
    const path = String(input)
    if (path.endsWith('/session/restore')) return reply(session)
    if (path === '/api/remote/goals?offset=0&limit=20') return reply({ items: [seeded, submitted], nextOffset: null })
    if (path === '/api/remote/goals/task-completed') return reply(seeded)
    return original(input, init)
  })
  render(<RemoteOperatorProvider><RemoteOperation/></RemoteOperatorProvider>)
  const example = await screen.findByRole('button', { name: /Example completed report/ })
  expect(within(example).getByText('Seeded demonstration')).toBeInTheDocument()
  expect(within(screen.getByRole('button', { name: /Operator completed report/ })).queryByText('Seeded demonstration')).toBeNull()
  fireEvent.click(example)
  expect(await screen.findByText('This built-in demonstration record is not evidence of a completed operator objective.')).toBeInTheDocument()
})

it('uses same-origin requests, keeps credentials out of browser storage, and logs out', async () => {
  const { calls } = transport()
  const storage = vi.spyOn(Storage.prototype, 'setItem')
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  expect(result.current.session?.actorId).toBe('operator')
  expect(calls.every(call => call.path.startsWith('/api/remote/'))).toBe(true)
  expect(calls.every(call => call.init?.credentials === 'same-origin')).toBe(true)
  expect(storage).not.toHaveBeenCalled()
  await act(async () => { await result.current.logout() })
  expect(result.current.session).toBeNull()
  expect(result.current.goals).toEqual([])
})

it('submits once with native idempotency and clears all state on an expired session', async () => {
  const { calls, fetcher } = transport()
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  await act(async () => { await result.current.submitGoal('Useful goal', 'Produce an authorized report') })
  const submitted = calls.find(call => call.path === '/api/remote/goals' && call.init?.method === 'POST')
  expect(new Headers(submitted?.init?.headers).get('Idempotency-Key')).toBeTruthy()
  expect(new Headers(submitted?.init?.headers).get('X-Jarvis-CSRF')).toBe(session.csrfToken)
  expect(result.current.selectedId).toBe('task-1')
  fetcher.mockImplementation(async () => reply(null, 401))
  await act(async () => { await result.current.refresh() })
  expect(result.current.session).toBeNull()
  expect(result.current.goals).toEqual([])
})

it('retains the same idempotency key after a lost submission acknowledgement', async () => {
  const { calls, fetcher } = transport()
  const original = fetcher.getMockImplementation()!
  let lost = true
  fetcher.mockImplementation(async (input, init) => {
    if (String(input) === '/api/remote/goals' && init?.method === 'POST' && lost) {
      lost = false; calls.push({ path: String(input), init }); throw new Error('network lost')
    }
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  await act(async () => { await expect(result.current.submitGoal('Useful goal', 'Report')).rejects.toThrow() })
  await act(async () => { await result.current.submitGoal('Useful goal', 'Report') })
  const writes = calls.filter(call => call.path === '/api/remote/goals' && call.init?.method === 'POST')
  expect(writes).toHaveLength(2)
  expect(new Headers(writes[0]?.init?.headers).get('Idempotency-Key'))
    .toBe(new Headers(writes[1]?.init?.headers).get('Idempotency-Key'))
})



it('keeps a newer login when an old mutation returns an expired-session response', async () => {
  const { fetcher } = transport()
  const original = fetcher.getMockImplementation()!
  let release!: (value: ReturnType<typeof reply>) => void
  fetcher.mockImplementation(async (input, init) => {
    if (String(input) === '/api/remote/goals' && init?.method === 'POST')
      return new Promise(resolve => { release = resolve })
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  let pending!: Promise<unknown>
  act(() => { pending = result.current.submitGoal('Useful goal', 'Report').catch(() => undefined) })
  await act(async () => { await result.current.logout(); await result.current.login('n'.repeat(43)) })
  await act(async () => { release(reply(null, 401)); await pending })
  expect(result.current.session?.actorId).toBe('operator')
  expect(result.current.error).toBeNull()
})

it('prevents duplicate logout and login while a sign-out response is pending', async () => {
  const { calls, fetcher } = transport()
  const original = fetcher.getMockImplementation()!
  let release!: (value: ReturnType<typeof reply>) => void
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).endsWith('/session/logout')) {
      calls.push({ path: String(input), init })
      return new Promise(resolve => { release = resolve })
    }
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  let pending!: Promise<unknown>
  act(() => { pending = result.current.logout().catch(() => undefined) })
  await act(async () => {
    await expect(result.current.logout()).rejects.toThrow()
    await expect(result.current.login('n'.repeat(43))).rejects.toThrow()
  })
  expect(calls.filter(call => call.path.endsWith('/session/logout'))).toHaveLength(1)
  await act(async () => { release(reply(true)); await pending })
  expect(result.current.session).toBeNull()
})

it('ignores old refresh responses after a newer authoritative refresh', async () => {
  const { fetcher } = transport()
  const original = fetcher.getMockImplementation()!
  let release!: (value: ReturnType<typeof reply>) => void
  let stale = true
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).endsWith('/status')) {
      if (stale) { stale = false; return new Promise(resolve => { release = resolve }) }
      return reply({ emergencyStop: true, simulatorStatus: 'stopped', recoveryStatus: 'none',
        eventSessionId: 'event-1', lastSequenceNumber: 2 })
    }
    return original(input, init)
  })
  let pending!: Promise<void>
  act(() => { pending = result.current.refresh() })
  await act(async () => { await result.current.refresh() })
  expect(result.current.status?.emergencyStop).toBe(true)
  await act(async () => { release(reply({ emergencyStop: false, simulatorStatus: 'idle',
    recoveryStatus: 'none', eventSessionId: 'event-1', lastSequenceNumber: 1 })); await pending })
  expect(result.current.status?.emergencyStop).toBe(true)
})



it('keeps a failed control error when a read started during that control finishes later', async () => {
  const { fetcher } = transport(), original = fetcher.getMockImplementation()!
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  let releaseControl!: (value: ReturnType<typeof reply>) => void
  let releaseRead!: (value: ReturnType<typeof reply>) => void
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).endsWith('/system/emergency-stop'))
      return new Promise(resolve => { releaseControl = resolve })
    if (String(input).endsWith('/status'))
      return new Promise(resolve => { releaseRead = resolve })
    return original(input, init)
  })
  let control!: Promise<unknown>, read!: Promise<void>
  act(() => { control = result.current.control('stop').catch(() => undefined) })
  act(() => { read = result.current.refresh() })
  await act(async () => { releaseControl(reply(null, 500)); await control })
  expect(result.current.error).toBeTruthy()
  await act(async () => { releaseRead(reply({ emergencyStop: false, simulatorStatus: 'idle',
    recoveryStatus: 'none', eventSessionId: 'event-1', lastSequenceNumber: 1 })); await read })
  expect(result.current.error).toBeTruthy()
})



it('retains authorized goals when optional system and agent views are denied', async () => {
  const { fetcher } = transport(), original = fetcher.getMockImplementation()!
  fetcher.mockImplementation(async (input, init) => {
    const path = String(input)
    if (path.endsWith('/status') || path.includes('/agents')) return reply(null, 403)
    if (path.startsWith('/api/remote/goals?')) return reply({ items: [
      { id: 'allowed-task', title: 'Authorized goal', status: 'queued' }], nextOffset: null })
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  expect(result.current.goals.map(task => task.id)).toEqual(['allowed-task'])
  expect(result.current.status).toBeNull()
  expect(result.current.error).toBeTruthy()
})



it('uses the recorded runtime version and replays the same command after lost acknowledgement', async () => {
  const { fetcher, calls } = transport(), original = fetcher.getMockImplementation()!
  let lost = true
  const run = { specification: { run_id: 'run-1', task_id: 'task-1', agent_id: 'builder' },
    state: 'queued', version: 2, recovery_status: 'none', latest_checkpoint_id: null }
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).includes('/runtime/runs?')) return reply({ items: [run], next_offset: null, total_count: 1 })
    if (String(input).endsWith('/runtime/commands')) {
      calls.push({ path: String(input), init })
      if (lost) { lost = false; throw new Error('lost acknowledgement') }
      return reply({ snapshot: { ...run, state: 'pause_requested', version: 3 } })
    }
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  await act(async () => { await expect(result.current.runtimeControl('run-1', 'request_pause')).rejects.toThrow() })
  await act(async () => { await result.current.refresh() })
  await act(async () => { await result.current.runtimeControl('run-1', 'request_pause') })
  const commands = calls.filter(call => call.path.endsWith('/runtime/commands'))
  const first = JSON.parse(String(commands[0]?.init?.body))
  const replay = JSON.parse(String(commands[1]?.init?.body))
  expect(first.expected_run_version).toBe(2)
  expect(first.command_type).toBe('request_pause')
  expect(replay).toEqual(first)
})

it('requires the displayed exact native plan hash before protected workspace approval', async () => {
  const { fetcher, calls } = transport(), original = fetcher.getMockImplementation()!
  const plan = { repository_id: 'jarvis', runtime_run_id: 'run-1', base_sha: 'a'.repeat(40),
    plan_hash: 'b'.repeat(64), task_id: 'task-1', repository_identity: 'github.com/example/jarvis',
    base_branch: 'main', branch: 'codex/jarvis-fixture', worktree_key: 'fixture', policy_digest: 'c'.repeat(64) }
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).endsWith('/workspaces/preview')) return reply(plan)
    if (String(input).endsWith('/workspaces/approve')) { calls.push({path: String(input), init}); return reply({
      approval_id: 'approval-1', plan, approved_by: 'operator', expires_at: '2026-10-27T12:15:00Z' }) }
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  await act(async () => { await result.current.previewWorkspace('jarvis', 'run-1', 'a'.repeat(40)) })
  await act(async () => { await expect(result.current.approveWorkspace('wrong')).rejects.toThrow() })
  expect(calls.filter(call => call.path.endsWith('/workspaces/approve'))).toHaveLength(0)
  await act(async () => { await result.current.approveWorkspace('b'.repeat(64)) })
  expect(result.current.workspaceApproval?.approval_id).toBe('approval-1')
  expect(JSON.parse(String(calls.find(call => call.path.endsWith('/workspaces/approve'))?.init?.body)))
    .toMatchObject({ runtime_run_id: 'run-1', expected_plan_hash: 'b'.repeat(64) })
})



it('creates a fresh explicit runtime intent after a definitive version conflict and refresh', async () => {
  const { fetcher, calls } = transport(), original = fetcher.getMockImplementation()!
  let version = 2, conflict = true
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).includes('/runtime/runs?')) return reply({ items: [{
      specification: { run_id: 'run-conflict', task_id: 'task-1', agent_id: 'builder' },
      state: 'queued', version }], next_offset: null, total_count: 1 })
    if (String(input).endsWith('/runtime/commands')) {
      calls.push({path: String(input), init})
      if (conflict) { conflict = false; version = 3
        return { ...reply(null, 409), json: async () => ({ error: {
          code: 'version_conflict', message: 'Expected run version differs' } }) } }
      return reply({ snapshot: { state: 'pause_requested', version: 4 } })
    }
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  await act(async () => { await expect(result.current.runtimeControl('run-conflict', 'request_pause')).rejects.toThrow() })
  await act(async () => { await result.current.refresh() })
  await act(async () => { await result.current.runtimeControl('run-conflict', 'request_pause') })
  const commands = calls.filter(call => call.path.endsWith('/runtime/commands'))
  const first = JSON.parse(String(commands[0]?.init?.body))
  const second = JSON.parse(String(commands[1]?.init?.body))
  expect(second.expected_run_version).toBe(3)
  expect(second.command_id).not.toBe(first.command_id)
})

it('invalidates an old workspace review as soon as a new preview starts', async () => {
  const { fetcher, calls } = transport(), original = fetcher.getMockImplementation()!
  const plan = { repository_id: 'jarvis', runtime_run_id: 'run-1', base_sha: 'a'.repeat(40),
    plan_hash: 'b'.repeat(64), task_id: 'task-1' }
  let release!: (value: ReturnType<typeof reply>) => void, previewCount = 0
  fetcher.mockImplementation(async (input, init) => {
    if (String(input).endsWith('/workspaces/preview')) {
      if (++previewCount === 1) return reply(plan)
      return new Promise(resolve => { release = resolve })
    }
    if (String(input).endsWith('/workspaces/approve')) { calls.push({path: String(input), init}); return reply({}) }
    return original(input, init)
  })
  const { result } = renderHook(useRemoteOperator, { wrapper: RemoteOperatorProvider })
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => { await result.current.login('t'.repeat(43)) })
  await act(async () => { await result.current.previewWorkspace('jarvis', 'run-1', 'a'.repeat(40)) })
  const staleApprove = result.current.approveWorkspace
  let pending!: Promise<void>
  act(() => { pending = result.current.previewWorkspace('jarvis', 'run-2', 'a'.repeat(40)) })
  try {
    await act(async () => { await expect(staleApprove('b'.repeat(64))).rejects.toThrow() })
    expect(calls.filter(call => call.path.endsWith('/workspaces/approve'))).toHaveLength(0)
  } finally {
    await act(async () => { release(reply({ ...plan, runtime_run_id: 'run-2', plan_hash: 'c'.repeat(64) })); await pending })
  }
})
