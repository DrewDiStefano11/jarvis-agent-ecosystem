import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { useAppStore } from '../src/state/AppStore'
import type { SystemStatus } from '../src/types/contracts'

vi.mock('../src/state/AppStore', () => ({ useAppStore: vi.fn() }))
const healthySystem = {
  status: 'healthy', databaseHealthy: true, schemaCurrent: true, emergencyStop: false,
  outboxExhaustedCount: 0, outboxPendingCount: 0, staleWorkerCount: 0, expiredLeaseCount: 0,
  simulator: { state: 'idle' }, resources: [],
  autonomousWorker: { enabled: false, status: 'disabled', providerReady: false },
} as unknown as SystemStatus
let store: ReturnType<typeof useAppStore>
function renderShell() { return render(<MemoryRouter><App/></MemoryRouter>) }
beforeEach(() => {
  store = {
    system: healthySystem, connection: 'connected', lastSync: '2026-10-06T14:00:00Z',
    loading: false, error: null, resyncRequired: false, tasks: [], agents: [], approvals: [], auditEvents: [],
    runtime: { actorId: '', identities: [], runs: [], executions: [], taskId: '', loading: false, error: null, nextOffset: null },
    action: vi.fn().mockResolvedValue({}), refresh: vi.fn().mockResolvedValue(undefined),
    selectTask: vi.fn(),
  } as unknown as ReturnType<typeof useAppStore>
  vi.mocked(useAppStore).mockImplementation(() => store)
  vi.spyOn(window, 'confirm').mockReturnValue(false)
})
test('no identity does not claim there are no active system runs', () => {
  renderShell()
  expect(screen.getByText('Select an identity to inspect runtime work')).toBeInTheDocument()
  expect(screen.queryByText('No active runs in this view')).not.toBeInTheDocument()
  expect(screen.getByText(/execution provenance is not available/)).toBeInTheDocument()
})
test('an empty authorized page discloses scope and pagination', () => {
  store.runtime = { ...store.runtime, actorId: 'operator', taskId: 'task-filter', nextOffset: 50 }
  renderShell()
  expect(screen.getByText('No active runs in this view')).toBeInTheDocument()
  expect(screen.getByText(/Filtered to the selected task/)).toBeInTheDocument()
  expect(screen.getByText(/More runs exist beyond this page/)).toBeInTheDocument()
})
test('an authorized failure is visible instead of an empty success state', () => {
  store.runtime = { ...store.runtime, actorId: 'operator', error: 'Access was revoked' }
  renderShell()
  expect(screen.getByRole('alert')).toHaveTextContent('Access was revoked')
  expect(screen.queryByText('No active runs in this view')).not.toBeInTheDocument()
})
test('stale state remains inspectable and offers HTTP refresh', async () => {
  store.connection = 'offline'
  renderShell()
  expect(screen.getByRole('status')).toHaveTextContent('Data may be stale')
  await userEvent.click(screen.getByRole('button', { name: 'Refresh state' }))
  expect(store.refresh).toHaveBeenCalledOnce()
  expect(screen.getByRole('heading', { name: 'System health' })).toBeInTheDocument()
})
test('unknown health never displays a clear system', () => {
  store.system = null
  renderShell()
  expect(screen.getByText(/System health could not be confirmed/)).toBeInTheDocument()
  expect(screen.queryByText('No system blockers reported')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Emergency stop' })).toBeDisabled()
})
test('degraded delivery and emergency stop appear before runtime work', () => {
  store.system = { ...healthySystem, emergencyStop: true, outboxExhaustedCount: 2 }
  renderShell()
  expect(screen.getByRole('link', { name: /2 event deliveries exhausted/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Emergency stop is active/ })).toBeInTheDocument()
})
test('declining system control confirmation never sends a command', async () => {
  renderShell()
  await userEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
  expect(window.confirm).toHaveBeenCalledOnce()
  expect(store.action).not.toHaveBeenCalled()
})
test('collapsed desktop navigation retains accessible destination names', async () => {
  renderShell()
  await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
  expect(screen.getByRole('button', { name: 'Expand sidebar' })).toHaveAttribute('aria-expanded', 'false')
  expect(screen.getAllByRole('link', { name: 'Office' })[0]).toHaveAttribute('title', 'Office')
  await act(async () => { await userEvent.click(screen.getByRole('button', { name: 'Expand sidebar' })) })
  expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toHaveAttribute('aria-expanded', 'true')
})

