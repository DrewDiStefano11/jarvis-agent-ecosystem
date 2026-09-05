import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { officePosition } from '../src/state/officeMotion'
import { useOfficeState } from '../src/state/useOfficeState'
import type { OfficePlacement, OfficeSnapshot } from '../src/types/office'
import catalog from '../../api/app/office/catalog.json'

const now = '2026-09-05T12:00:00Z'
const placement: OfficePlacement = { identityId: 'real-identity', displayName: 'Local planner', lifecycleState: 'active', enabled: true, stationId: 'POSITION_022', spriteId: 'agent-sheet-01', position: { x: 100, y: 100 }, motion: { originId: 'POSITION_022', destinationId: 'POSITION_028', points: [{ x: 100, y: 100 }, { x: 200, y: 100 }, { x: 200, y: 200 }], doorIds: [], startedAt: now, durationMs: 2000, stoppedAt: null }, movementState: 'moving', activity: 'working', version: 2, updatedAt: now }
const snapshot: OfficeSnapshot = { serverTime: now, catalog, placements: [placement], placementVersions: { 'real-identity': 2 }, emergencyStop: false }
const response = (data: unknown) => ({ ok: true, status: 200, json: async () => ({ data }) }) as Response
afterEach(() => vi.unstubAllGlobals())

test('server clock follows path length, clamps arrival, and freezes durable stop without inventing work', () => {
  expect(officePosition(placement, Date.parse(now) + 500)).toEqual({ point: { x: 150, y: 100 }, direction: 'east' })
  expect(officePosition(placement, Date.parse(now) + 1500)).toEqual({ point: { x: 200, y: 150 }, direction: 'south' })
  expect(officePosition(placement, Date.parse(now) + 5000).point).toEqual({ x: 200, y: 200 })
  const stopped = { ...placement, motion: { ...placement.motion!, stoppedAt: '2026-09-05T12:00:00.500Z' } }
  expect(officePosition(stopped, Date.parse(now) + 999000).point).toEqual({ x: 150, y: 100 })
  expect(officePosition({ ...placement, enabled: false }, Date.parse(now) + 1500).point).toEqual(placement.position)
})

test('office loads only after activation and rejects an older overlapping refresh', async () => {
  let finishOld: (value: Response) => void = () => undefined
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finishOld = resolve })).mockResolvedValue(response({ ...snapshot, placements: [] }))
  vi.stubGlobal('fetch', fetcher)
  const { result, rerender } = renderHook(({ sync }) => useOfficeState(sync), { initialProps: { sync: 'initial' } })
  expect(fetcher).not.toHaveBeenCalled()
  let old!: Promise<void>
  act(() => { old = result.current.load() })
  rerender({ sync: 'event-2' })
  await waitFor(() => expect(result.current.snapshot?.placements).toEqual([]))
  await act(async () => { finishOld(response(snapshot)); await old })
  expect(result.current.snapshot?.placements).toEqual([])
})

test('lost acknowledgement retains one exact command for explicit retry and never automatically posts on resync', async () => {
  const posts: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posts.push(String(init.body))
      if (posts.length === 1) throw new TypeError('Connection lost')
      return response({ commandId: 'accepted', identityId: placement.identityId, action: 'stop', version: 3 })
    }
    return response(snapshot)
  }))
  const { result, rerender } = renderHook(({ sync }) => useOfficeState(sync), { initialProps: { sync: 'one' } })
  await act(() => result.current.load())
  await act(() => result.current.command(placement.identityId, { action: 'stop', expectedVersion: 2 }))
  expect(result.current.pending?.command.commandId).toMatch(/^office-/)
  expect(result.current.error).toBe('Connection lost')
  rerender({ sync: 'two' })
  await act(async () => { await Promise.resolve() })
  expect(posts).toHaveLength(1)
  await act(() => result.current.retry())
  expect(posts).toHaveLength(2)
  expect(posts[1]).toBe(posts[0])
  expect(JSON.parse(posts[0]!)).toEqual({ action: 'stop', expectedVersion: 2, commandId: expect.any(String) })
  expect(result.current.pending).toBeNull()
})

test('rejected stale command refreshes authoritative placement and remains an actionable error', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? ({ ok: false, status: 409, json: async () => ({ error: { code: 'OFFICE_VERSION_CONFLICT', message: 'Office state changed. Refresh before issuing this command again.' } }) }) as Response
    : response({ ...snapshot, placements: [{ ...placement, version: 9 }] })))
  const { result } = renderHook(() => useOfficeState(null))
  await act(() => result.current.command(placement.identityId, { action: 'stop', expectedVersion: 2 }))
  expect(result.current.pending).toBeNull()
  expect(result.current.snapshot?.placements[0]?.version).toBe(9)
  expect(result.current.error).toMatch(/Office state changed/)
  await act(() => result.current.load())
  expect(result.current.error).toMatch(/Office state changed/)
})
