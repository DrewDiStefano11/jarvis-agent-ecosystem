import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { request } from '../api/client'
import { useResultsState } from './useResultsState'
import type { ResultReviewItem } from '../types/results'

vi.mock('../api/client', () => ({ request: vi.fn(), ApiError: class extends Error {} }))
beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })
const item = { execution: { executionId: 'exec-1', runtimeRunId: 'run-1', resultHash: 'a'.repeat(64) }, taskTitle: 'QC checklist', taskUpdatedAt: '2026-11-06T12:00:00Z', runVersion: 9, checkpointId: 'checkpoint-1' } as ResultReviewItem

it('retains the exact decision across lost acknowledgement and reload without automatic dispatch', async () => {
  vi.mocked(request).mockImplementation(async path => {
    if (path === '/api/results/operator') return { actorId: 'reviewer-1' }
    if (path.endsWith('/review')) throw new TypeError('ack lost')
    return []
  })
  const hook = renderHook(() => useResultsState())
  await act(async () => { await hook.result.current.refresh() })
  await act(async () => { await hook.result.current.decide(item, 'accept', 'Reviewed against the scope.').catch(() => undefined) })
  const first = vi.mocked(request).mock.calls.find(call => call[0].endsWith('/review'))?.[1]?.body
  expect(first).toBeTruthy()
  expect(hook.result.current.pending?.decision).toBe('accept')
  hook.unmount()
  vi.mocked(request).mockClear()
  const reloaded = renderHook(() => useResultsState())
  expect(request).not.toHaveBeenCalled()
  expect(reloaded.result.current.pending?.decision).toBe('accept')
  await act(async () => { await reloaded.result.current.retry().catch(() => undefined) })
  expect(vi.mocked(request).mock.calls.find(call => call[0].endsWith('/review'))?.[1]?.body).toBe(first)
})

it('persists before sending and refuses to dispatch when browser recovery storage fails', async () => {
  vi.mocked(request).mockResolvedValue({ actorId: 'reviewer-1' })
  const hook = renderHook(() => useResultsState())
  await act(async () => { await hook.result.current.refresh() })
  const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage denied') })
  vi.mocked(request).mockClear()
  await act(async () => { await hook.result.current.decide(item, 'reject', 'Reviewed against the scope.').catch(() => undefined) })
  expect(request).not.toHaveBeenCalled()
  expect(hook.result.current.error).toContain('recovery')
  storage.mockRestore()
})
