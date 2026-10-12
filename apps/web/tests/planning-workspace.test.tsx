import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'
import { Runtime } from '../src/pages/Runtime'
import { useAppStore } from '../src/state/AppStore'
import { request } from '../src/api/client'
import { newPlanningSubmission, submitPlanning } from '../src/api/planning'
import type { Task } from '../src/types/contracts'
import type { RuntimeRun } from '../src/types/runtime'

vi.mock('../src/state/AppStore', () => ({ useAppStore: vi.fn() }))
vi.mock('../src/api/client', () => ({ request: vi.fn() }))
vi.mock('../src/api/planning', async importOriginal => {
  const actual = await importOriginal<typeof import('../src/api/planning')>()
  return { ...actual, newPlanningSubmission: vi.fn(actual.newPlanningSubmission), submitPlanning: vi.fn() }
})
vi.mock('../src/state/planningRecovery', () => ({ readPlanningRecovery: () => ({ items: [], warning: '' }), rememberPlanningSubmission: vi.fn().mockResolvedValue(''), forgetPlanningSubmission: vi.fn().mockReturnValue(''), restorePlanningSubmission: vi.fn() }))

const task = { id: 'task-qa', title: 'Bounded request', description: 'Produce an inspectable local plan.', request: 'Bounded request', status: 'queued', updatedAt: '2026-10-12T12:00:00Z' } as Task
const identity = { id: 'worker', display_name: 'Configured planner', stable_key: 'planner', lifecycle_state: 'active', is_enabled: true, operational_status: 'idle', agent_type: 'worker', description: '', version: 1 }
let store: ReturnType<typeof useAppStore>
const renderPlanning = (props = {}) => render(<MemoryRouter><Runtime {...props}/></MemoryRouter>)
beforeEach(() => {
  vi.clearAllMocks()
  store = {
    tasks: [task], loading: false, error: null, resyncRequired: false, connection: 'connected',
    system: { emergencyStop: false, autonomousWorker: { enabled: true, status: 'healthy', workerActorId: 'worker', providerReady: true } },
    runtime: { taskId: task.id, actorId: identity.id, identities: [identity], executions: [], runs: [], nextOffset: null, pagesLoaded: 1, loadIdentities: vi.fn().mockResolvedValue(undefined), selectActor: vi.fn(), setTaskId: vi.fn(), refreshRuntime: vi.fn().mockResolvedValue(undefined) },
    tools: { executions: [] }, decomposition: { record: null, error: '' }, coordination: { record: null, error: '' },
    refresh: vi.fn().mockResolvedValue(undefined), selectTask: vi.fn(),
  } as unknown as ReturnType<typeof useAppStore>
  vi.mocked(useAppStore).mockImplementation(() => store)
  vi.mocked(request).mockResolvedValue({ actorId: 'worker', workerActorConfigured: true })
  vi.mocked(submitPlanning).mockRejectedValue(new Error('Acknowledgement interrupted'))
})
async function makeReady() {
  await userEvent.selectOptions(screen.getByLabelText('Target agent'), 'worker')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeEnabled())
}

test('opening and selecting planning scope never prepares or queues work', async () => {
  renderPlanning(); await makeReady()
  expect(request).toHaveBeenCalledWith('/api/local-planning/setup?taskId=task-qa')
  expect(vi.mocked(request).mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  expect(submitPlanning).not.toHaveBeenCalled()
})

test('stale and emergency-stopped views disable preparation and queueing', async () => {
  const view = renderPlanning(); await makeReady()
  store.connection = 'offline'
  view.rerender(<MemoryRouter><Runtime/></MemoryRouter>)
  expect(screen.getByRole('button', { name: 'Prepare local planner for this task' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Queue local plan' }))
  expect(submitPlanning).not.toHaveBeenCalled()
  store.connection = 'connected'; store.system = { ...store.system!, emergencyStop: true }
  view.rerender(<MemoryRouter><Runtime/></MemoryRouter>)
  expect(screen.getByRole('button', { name: 'Prepare local planner for this task' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Queue local plan' })).toBeDisabled()
})

test('explicit setup posts only the selected persisted task and preserves shared scope', async () => {
  renderPlanning(); await waitFor(() => expect(screen.getByRole('button', { name: 'Prepare local planner for this task' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: 'Prepare local planner for this task' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/api/local-planning/setup', { method: 'POST', body: JSON.stringify({ taskId: task.id }) }))
  expect(store.runtime.selectActor).toHaveBeenCalledWith('worker')
  expect(submitPlanning).not.toHaveBeenCalled()
})

test('an uncertain submission locks inputs and retries the same immutable submission', async () => {
  renderPlanning(); await makeReady()
  await userEvent.click(screen.getByRole('button', { name: 'Queue local plan' }))
  await screen.findByText('Acknowledgement interrupted')
  expect(screen.getByLabelText('Task and history')).toBeDisabled()
  expect(screen.getByLabelText('Act as local identity')).toBeDisabled()
  expect(screen.getByLabelText('Target agent')).toBeDisabled()
  expect(screen.getByLabelText('Plan type')).toBeDisabled()
  expect(screen.getByRole('button', { name: 'New task' })).toBeDisabled()
  const first = vi.mocked(submitPlanning).mock.calls[0]![0]
  expect(first.task.id).toBe(task.id)
  expect(first.actorId).toBe('worker')
  expect(first.targetId).toBe('worker')
  vi.mocked(submitPlanning).mockResolvedValue({ snapshot: { specification: { run_id: 'run-accepted' } } as RuntimeRun, contextCategories: ['manual_note'] })
  await userEvent.click(screen.getByRole('button', { name: 'Retry same submission' }))
  await screen.findByText(/Queued run-accepted/)
  expect(newPlanningSubmission).toHaveBeenCalledTimes(1)
  expect(vi.mocked(submitPlanning).mock.calls[1]![0]).toBe(first)
})

test('embedded creation and correction use callbacks without route navigation', async () => {
  store.tasks = [{ ...task, status: 'completed' }]
  const create = vi.fn(); const correct = vi.fn()
  renderPlanning({ onCreateTask: create, onCorrectTask: correct })
  await userEvent.click(screen.getByRole('button', { name: 'New task' }))
  expect(create).toHaveBeenCalledOnce()
  await userEvent.click(screen.getByRole('button', { name: 'Revise task input' }))
  expect(correct).toHaveBeenCalledWith(task.id)
  expect(submitPlanning).not.toHaveBeenCalled()
})

test('task inspectors lock the request so shared planning evidence cannot drift to another task', async () => {
  renderPlanning({ lockedTaskId: task.id }); await makeReady()
  expect(screen.getByLabelText('Task and history')).toBeDisabled()
  expect(screen.getByLabelText('Task and history')).toHaveValue(task.id)
  expect(store.runtime.setTaskId).not.toHaveBeenCalled()
  expect(submitPlanning).not.toHaveBeenCalled()
})
