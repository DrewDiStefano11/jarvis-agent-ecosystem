import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useGoalsState } from '../src/state/useGoalsState'

const goal = { id: 'goal-one', conversationId: 'conversation-one', taskId: 'task-one', title: 'Review schedules', disposition: 'draft', createdAt: '2026-11-06T12:00:00Z', messages: [{ id: 'message-one', role: 'user', content: 'Review schedules', createdAt: '2026-11-06T12:00:00Z' }] }
const reply = (data: unknown) => ({ ok: true, json: async () => ({ data }) }) as Response
beforeEach(() => sessionStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

test('saves one draft, selects its persisted conversation and loads history', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url, init) => reply(init?.method === 'POST' ? goal : [])))
  const { result } = renderHook(() => useGoalsState())
  await act(async () => { await result.current.submit('Review schedules') })
  expect(result.current.selected?.id).toBe(goal.id)
  expect(result.current.items).toEqual([goal])
  expect(result.current.pending).toBeNull()
  expect(sessionStorage.getItem('jarvis.goal.pending.v1')).toBeNull()
})

test('lost acknowledgement and reload preserve exact key and text without auto submission', async () => {
  let lose = true
  const fetcher = vi.fn(async (_url, init) => {
    if (init?.method !== 'POST') return reply([])
    if (lose) throw new Error('Connection interrupted')
    return reply(goal)
  })
  vi.stubGlobal('fetch', fetcher)
  const first = renderHook(() => useGoalsState())
  await act(async () => { await expect(first.result.current.submit('Review schedules')).rejects.toThrow('Connection interrupted') })
  const pending = first.result.current.pending!
  first.unmount()
  const second = renderHook(() => useGoalsState())
  expect(second.result.current.pending).toEqual(pending)
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  lose = false
  await act(async () => { await second.result.current.submit('Review schedules') })
  const posts = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(posts[0]![1]!.headers).toEqual(posts[1]![1]!.headers)
  expect(second.result.current.pending).toBeNull()
})

test('changed content cannot silently replace an uncertain submission', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('Offline') }))
  const { result } = renderHook(() => useGoalsState())
  await act(async () => { await expect(result.current.submit('Review schedules')).rejects.toThrow() })
  await act(async () => { await expect(result.current.submit('Different scope')).rejects.toThrow(/pending/i) })
})

test('history restoration reports errors and ignores stale selection responses', async () => {
  let finish: (value: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (url) => String(url).endsWith('old') ? await new Promise<Response>(resolve => { finish = resolve }) : reply(goal)))
  const { result } = renderHook(() => useGoalsState())
  let old: Promise<void>
  act(() => { old = result.current.open('old') })
  await act(async () => { await result.current.open('new') })
  await act(async () => { finish(reply({ ...goal, id: 'old' })); await old })
  await waitFor(() => expect(result.current.selected?.id).toBe('goal-one'))
})

test('unavailable session storage blocks dispatch with a visible error', async () => {
  vi.stubGlobal('fetch', vi.fn())
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage unavailable') })
  const { result } = renderHook(() => useGoalsState())
  await act(async () => { await expect(result.current.submit('Review schedules')).rejects.toThrow() })
  expect(result.current.error).toMatch(/retain|storage/i)
  expect(fetch).not.toHaveBeenCalled()
  write.mockRestore()
})

test('a slow submission cannot replace a conversation opened later', async () => {
  let finish: (value: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (_url, init) => init?.method === 'POST' ? await new Promise<Response>(resolve => { finish = resolve }) : reply({ ...goal, id: 'other-conversation' })))
  const { result } = renderHook(() => useGoalsState())
  let submission: Promise<unknown>
  act(() => { submission = result.current.submit('Review schedules') })
  await act(async () => { await result.current.open('other-conversation') })
  await act(async () => { finish(reply(goal)); await submission })
  expect(result.current.selected?.id).toBe('other-conversation')
  expect(result.current.items[0]?.id).toBe('goal-one')
})


test('unicode input uses backend code point limits before freezing a request', async () => {
  vi.stubGlobal('fetch', vi.fn())
  const { result } = renderHook(() => useGoalsState())
  await act(async () => { await expect(result.current.submit('😀a')).rejects.toThrow(/three/i) })
  expect(result.current.pending).toBeNull()
  expect(fetch).not.toHaveBeenCalled()
})

test('definitive request validation failure releases input for correction', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 422, json: async () => ({ error: { code: 'VALIDATION_ERROR', message: 'Request rejected before saving' } }) })))
  const { result } = renderHook(() => useGoalsState())
  await act(async () => { await expect(result.current.submit('Review schedules')).rejects.toThrow('Request rejected') })
  expect(result.current.pending).toBeNull()
  expect(sessionStorage.getItem('jarvis.goal.pending.v1')).toBeNull()
})

test('a history read overlapping commit cannot remove newly saved work', async () => {
  let finishPost: (value: Response) => void = () => {}
  let finishRead: (value: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (_url, init) => await new Promise<Response>(resolve => { if (init?.method === 'POST') finishPost = resolve; else finishRead = resolve })))
  const { result } = renderHook(() => useGoalsState())
  let submission: Promise<unknown>, refresh: Promise<void>
  act(() => { submission = result.current.submit('Review schedules') })
  act(() => { refresh = result.current.refresh() })
  await act(async () => { finishPost(reply(goal)); await submission })
  await act(async () => { finishRead(reply([])); await refresh })
  expect(result.current.items).toEqual([goal])
})
