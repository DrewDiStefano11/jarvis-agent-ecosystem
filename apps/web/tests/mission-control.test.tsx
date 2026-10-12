import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, useNavigate } from 'react-router-dom'
import { beforeAll, beforeEach, expect, test, vi } from 'vitest'
import App from '../src/App'
import { useAppStore } from '../src/state/AppStore'
import type { SystemStatus } from '../src/types/contracts'
import type { Task } from '../src/types/contracts'

vi.mock('../src/state/AppStore', () => ({ useAppStore: vi.fn() }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new Event('close')) }
})
const healthySystem = {
  status: 'healthy', databaseHealthy: true, schemaCurrent: true, emergencyStop: false,
  outboxExhaustedCount: 0, outboxPendingCount: 0, staleWorkerCount: 0, expiredLeaseCount: 0,
  simulator: { state: 'idle' }, resources: [],
  autonomousWorker: { enabled: false, status: 'disabled', providerReady: false },
} as unknown as SystemStatus
let store: ReturnType<typeof useAppStore>
function renderShell() { return render(<MemoryRouter><App/></MemoryRouter>) }
test('new task works in place and preserves a draft after closing', async () => {
  renderShell()
  await userEvent.click(screen.getByRole('button', { name: 'New task' }))
  const drawer = within(screen.getByRole('dialog', { name: 'New task' }))
  await userEvent.type(drawer.getByLabelText('Title'), 'Preserve my request')
  await userEvent.click(drawer.getByRole('button', { name: 'Close New task' }))
  await userEvent.click(screen.getByRole('button', { name: 'New task' }))
  expect(within(screen.getByRole('dialog', { name: 'New task' })).getByLabelText('Title')).toHaveValue('Preserve my request')
  expect(screen.getByRole('heading', { name: 'Mission Control' })).toBeInTheDocument()
  expect(store.action).not.toHaveBeenCalled()
})
test('stale state disables in-place task creation without hiding the request', async () => {
  store.connection = 'offline'
  renderShell()
  await userEvent.click(screen.getByRole('button', { name: 'New task' }))
  const drawer = within(screen.getByRole('dialog', { name: 'New task' }))
  expect(drawer.getByRole('button', { name: 'Create task' })).toBeDisabled()
  expect(drawer.getByRole('status')).toHaveTextContent('Reconnect')
})
test('chart filters include matching child records and never issue a runtime command', async () => {
  const record = { title: 'Root request', id: 'root', status: 'queued', parentTaskId: null, updatedAt: '2026-10-12', progress: 0, blockedBy: [], description: '', statusMessage: 'Queued' } as unknown as Task
  store.tasks = [record, { ...record, id: 'child', title: 'Queued child', parentTaskId: 'root' }, { ...record, id: 'done', title: 'Finished request', status: 'completed' }]
  renderShell()
  const legend = within(screen.getByRole('group', { name: 'Filter task records by status' }))
  await userEvent.click(legend.getByRole('button', { name: 'Queued, 2 task records' }))
  expect(screen.getByRole('button', { name: 'Inspect Queued child' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Inspect Finished request' })).not.toBeInTheDocument()
  expect(store.action).not.toHaveBeenCalled()
})
test('choosing an identity updates shared inspection scope without navigating or queueing', async () => {
  store.runtime = { ...store.runtime, identityLoaded: true, identities: [{ id: 'registered', display_name: 'Real local identity', stable_key: 'local-fixture', lifecycle_state: 'active', operational_status: 'idle', is_enabled: true, agent_type: 'worker', description: 'Isolated test identity', version: 1 }], selectActor: vi.fn() }
  renderShell()
  await userEvent.click(screen.getByRole('button', { name: 'Inspect runtime as Real local identity' }))
  expect(store.runtime.selectActor).toHaveBeenCalledWith('registered')
  await userEvent.click(screen.getByRole('button', { name: 'Choose identity' }))
  await userEvent.selectOptions(screen.getByLabelText('Inspect as identity'), 'registered')
  expect(store.runtime.selectActor).toHaveBeenCalledWith('registered')
  expect(store.action).not.toHaveBeenCalled()
  expect(screen.getByRole('heading', { name: 'Mission Control' })).toBeInTheDocument()
})
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
  expect(screen.getByText('Approval snapshot unavailable')).toBeInTheDocument()
  expect(screen.queryByText('No pending decisions')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Emergency stop' })).toBeDisabled()
})
test('degraded delivery and emergency stop appear before runtime work', () => {
  store.system = { ...healthySystem, emergencyStop: true, outboxExhaustedCount: 2 }
  renderShell()
  expect(screen.getByRole('button', { name: /2 event deliveries exhausted/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /Emergency stop is active/ })).toBeInTheDocument()
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
  expect(screen.getByRole('button', { name: /Emergency stop is active/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /2 event deliveries exhausted/ })).toBeInTheDocument()
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


test('shell blocks stale resume while preserving emergency stop and reconciled resume', async () => {
  vi.mocked(window.confirm).mockReturnValue(true)
  store.system = { ...healthySystem, emergencyStop: true }
  store.error = 'Read failed'
  const view = renderShell()
  const rerender = () => view.rerender(<MemoryRouter><App/></MemoryRouter>)
  expect(screen.getByRole('button', { name: 'Resume system' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Resume system' }))
  expect(store.action).not.toHaveBeenCalled()
  store.error = null
  store.resyncRequired = true
  rerender()
  expect(screen.getByRole('button', { name: 'Resume system' })).toBeDisabled()
  store.resyncRequired = false
  store.connection = 'reconnecting'
  rerender()
  expect(screen.getByRole('button', { name: 'Resume system' })).toBeDisabled()
  store.system = healthySystem
  rerender()
  await userEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
  expect(store.action).toHaveBeenCalledWith('/api/system/emergency-stop')
  store.system = { ...healthySystem, emergencyStop: true }
  store.connection = 'connected'
  rerender()
  await userEvent.click(screen.getByRole('button', { name: 'Resume system' }))
  expect(store.action).toHaveBeenCalledWith('/api/system/resume')
})
