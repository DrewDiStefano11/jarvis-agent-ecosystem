import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, useNavigate } from 'react-router-dom'
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


test('failed refresh keeps known blockers visible with a stale qualifier', () => {
  store.system = { ...healthySystem, emergencyStop: true, outboxExhaustedCount: 2 }
  store.error = 'Refresh failed'
  renderShell()
  expect(screen.getByRole('link', { name: /Emergency stop is active/ })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /2 event deliveries exhausted/ })).toBeInTheDocument()
  expect(screen.getByText(/Last-known blockers shown/)).toBeInTheDocument()
})
test('mobile More reveals destinations after its trigger in keyboard order', async () => {
  renderShell()
  const mobile = within(screen.getByRole('navigation', { name: 'Mobile primary' }))
  expect(mobile.queryByRole('link', { name: 'Office' })).not.toBeInTheDocument()
  await userEvent.click(mobile.getByRole('button', { name: 'More navigation' }))
  await userEvent.tab()
  expect(mobile.getByRole('link', { name: 'Agents' })).toHaveFocus()
})
function QueryNavigation() {
  const navigate = useNavigate()
  return <><Link to="/tasks?create=1">Creation deep link</Link><Link to="/tasks">Clear query</Link><button onClick={() => navigate(-1)}>History back</button></>
}
test('creation follows query navigation and Back while Tasks stays mounted', async () => {
  render(<MemoryRouter initialEntries={['/tasks']}><App/><QueryNavigation/></MemoryRouter>)
  expect(screen.queryByLabelText('Title')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('link', { name: 'Creation deep link' }))
  expect(screen.getByLabelText('Title')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('link', { name: 'Clear query' }))
  expect(screen.queryByLabelText('Title')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'History back' }))
  expect(screen.getByLabelText('Title')).toBeInTheDocument()
})

test('approval count is included in desktop and mobile accessible link names', () => {
  store.approvals = [{ status: 'pending' }, { status: 'pending' }] as typeof store.approvals
  renderShell()
  expect(screen.getAllByRole('link', { name: 'Approvals, 2 pending approval records' })).toHaveLength(2)
})
