import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { useGoalPlanningState } from './useGoalPlanningState'

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })
test('an exact captured consent retry resolves even after current worker eligibility changes', async () => {
  let unavailable = false
  let lose = true
  const view = () => ({ id: 'policy-one', revision: 1, enabled: true, policyHash: 'a'.repeat(64), admittedGoals: 0, maximumGoals: 2, expiresAt: '2099-01-01T00:00:00Z', unavailableReason: unavailable ? 'worker_configuration_changed' : null })
  const response = (data: unknown) => ({ ok: true, json: async () => ({ data }) }) as Response
  const fetcher = vi.fn(async (url, init) => {
    if (String(url).endsWith('/operator')) return response({ actorId: 'operator-one' })
    if (init?.method === 'POST') {
      if (lose) throw new Error('Acknowledgement lost')
      return response(view())
    }
    return response(view())
  })
  vi.stubGlobal('fetch', fetcher)
  const first = renderHook(() => useGoalPlanningState())
  await act(async () => { await first.result.current.refresh() })
  await act(async () => { await first.result.current.configure(2) })
  const captured = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!
  expect(first.result.current.pending).not.toBeNull()
  first.unmount()
  lose = false; unavailable = true
  const restored = renderHook(() => useGoalPlanningState())
  await act(async () => { await restored.result.current.refresh() })
  expect(restored.result.current.policy?.unavailableReason).toBe('worker_configuration_changed')
  await act(async () => { await restored.result.current.configure(20) })
  const posts = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(posts[1]![1]!.body).toBe(captured.body)
  expect(posts[1]![1]!.headers).toEqual(captured.headers)
  expect(restored.result.current.pending).toBeNull()
})
