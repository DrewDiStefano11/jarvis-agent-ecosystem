import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'
import { System } from '../src/pages/System'
import { useAppStore } from '../src/state/AppStore'
import type { SystemStatus } from '../src/types/contracts'

vi.mock('../src/state/AppStore', () => ({ useAppStore: vi.fn() }))
const snapshot = { status: 'healthy', databaseHealthy: true, schemaCurrent: true, databaseRevision: 'merged-revision', emergencyStop: false, activeWorkerCount: 2, staleWorkerCount: 0, activeLeaseCount: 3, expiredLeaseCount: 0, outboxPendingCount: 4, outboxExhaustedCount: 0, resources: [{ name: 'CPU', value: 'fixture 18%' }], simulator: { state: 'idle' }, contextAssembler: { state: 'ready' }, autonomousWorker: { enabled: false, status: 'disabled', modelExecutionMode: 'disabled', providerReady: false, workerActorId: 'operator' } } as unknown as SystemStatus
let store: ReturnType<typeof useAppStore>
const show = () => render(<MemoryRouter><System/></MemoryRouter>)
beforeEach(() => {
  store = { system: snapshot, connection: 'connected', lastSync: '2026-10-06T15:00:00Z', error: null, resyncRequired: false, action: vi.fn().mockResolvedValue({}), refresh: vi.fn().mockResolvedValue(undefined) } as unknown as typeof store
  vi.mocked(useAppStore).mockImplementation(() => store)
  vi.spyOn(window, 'confirm').mockReturnValue(false)
})

test('unknown snapshot never invents zero counters, clear recovery, or provider readiness', () => {
  store.system = null
  show()
  expect(screen.getByRole('alert')).toHaveTextContent('System health is unavailable')
  expect(screen.getByRole('button', { name: 'Emergency stop' })).toBeDisabled()
  expect(screen.queryByText('0 pending / 0 exhausted')).not.toBeInTheDocument()
  expect(screen.queryByText('Ready')).not.toBeInTheDocument()
})

test('health rows preserve real zeros and do not include resource fixtures', () => {
  show()
  expect(screen.getByText('4 pending / 0 exhausted')).toBeInTheDocument()
  expect(screen.getByText('2 active / 0 stale')).toBeInTheDocument()
  expect(screen.queryByText('fixture 18%')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open Planning →' })).toBeInTheDocument()
})

test('failed synchronization preserves last-known blockers and HTTP refresh', async () => {
  store.system = { ...snapshot, emergencyStop: true, expiredLeaseCount: 1, staleWorkerCount: 1 }
  store.error = 'Refresh unavailable'
  show()
  expect(screen.getByText(/Last-known data may be outdated/)).toHaveTextContent('Refresh unavailable')
  expect(screen.getByText(/Emergency stop is active/)).toBeInTheDocument()
  expect(screen.getByText(/expired task lease is awaiting recovery/)).toBeInTheDocument()
  expect(screen.getByText('Last-known backend status: healthy')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Refresh state' }))
  expect(store.refresh).toHaveBeenCalledOnce()
})

test('declining stop or reset confirmation sends no command', async () => {
  show()
  await userEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
  fireEvent.click(screen.getByText('Demonstration controls'))
  await userEvent.click(screen.getByRole('button', { name: 'Reset demo' }))
  expect(window.confirm).toHaveBeenCalledTimes(2)
  expect(store.action).not.toHaveBeenCalled()
})

test('confirmed stop remains pending until acknowledgement and does not repeat writes', async () => {
  let resolve!: () => void
  vi.mocked(store.action).mockReturnValue(new Promise<void>(finish => { resolve = finish }))
  vi.mocked(window.confirm).mockReturnValue(true)
  show()
  await userEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
  expect(screen.getByRole('button', { name: 'Awaiting acknowledgement…' })).toBeDisabled()
  expect(store.action).toHaveBeenCalledExactlyOnceWith('/api/system/emergency-stop', undefined)
  await act(async () => resolve())
  expect(screen.getByRole('status')).toHaveTextContent('Emergency stop request acknowledged')
})

test('emergency stop disables demo controls that can reset its flag', () => {
  store.system = { ...snapshot, emergencyStop: true }
  show()
  fireEvent.click(screen.getByText('Demonstration controls'))
  const demo = within(document.querySelector<HTMLElement>('.system-demonstration')!)
  expect(demo.getByText(/Demo controls are unavailable during emergency stop/)).toBeInTheDocument()
  demo.getAllByRole('button').forEach(button => expect(button).toBeDisabled())
  expect(screen.getByRole('button', { name: 'Resume system' })).toBeEnabled()
})

test('stale snapshots cannot enable reset while the current stop state is unknown', () => {
  store.connection = 'reconnecting'
  show()
  fireEvent.click(screen.getByText('Demonstration controls'))
  expect(screen.getByRole('button', { name: 'Reset demo' })).toBeDisabled()
  expect(screen.getByText('Refresh state before using demonstration controls.')).toBeInTheDocument()
})

test('lost acknowledgement shows uncertainty and no automatic replay', async () => {
  vi.mocked(store.action).mockRejectedValue(new TypeError('Connection lost'))
  vi.mocked(window.confirm).mockReturnValue(true)
  show()
  await userEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
  expect(screen.getByRole('alert')).toHaveTextContent('Request outcome could not be confirmed. Refresh state before retrying.')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(store.action).toHaveBeenCalledOnce()
})

test('technical identifiers remain available behind a native disclosure', () => {
  show()
  const detail = document.querySelector<HTMLDetailsElement>('.system-technical')!
  expect(detail.open).toBe(false)
  fireEvent.click(screen.getByText('Technical details'))
  expect(detail.open).toBe(true)
  expect(within(detail).getByText('merged-revision (current)')).toBeInTheDocument()
  expect(within(detail).getByText('operator')).toBeInTheDocument()
})
