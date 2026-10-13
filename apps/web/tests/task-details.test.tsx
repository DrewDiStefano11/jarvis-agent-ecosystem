import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'
import { TaskDetails } from '../src/components/TaskDetails'
import { useAppStore } from '../src/state/AppStore'
import type { Approval, AuditEvent, Task } from '../src/types/contracts'

vi.mock('../src/state/AppStore', () => ({ useAppStore: vi.fn() }))
const task = { id: 'task-1', schemaVersion: '1.0', description: 'Request', childTaskIds: [], projectId: null, createdBy: 'operator', dependencies: [], startedAt: null, completedAt: null, title: 'First request', request: 'Review scope', priority: 'high', status: 'queued', progress: 0, statusMessage: 'Queued', parentTaskId: null, assignedAgentIds: [], assignedManagerId: null, blockedBy: [], approvalIds: ['own'], artifactIds: [], retryCount: 0, maxRetries: 2, result: null, error: null, createdAt: '2026-10-12T12:00:00Z', updatedAt: '2026-10-12T12:00:00Z' } as Task
const approval = { id: 'own', reviewedBy: null, reviewedAt: null, decisionNote: null, taskId: task.id, title: 'Own decision', status: 'pending', riskLevel: 'yellow', description: 'Decision', reason: 'Review', actionType: 'simulated_publish', requestedByAgentId: 'agent', exactActionPreview: 'Preview', affectedResources: [], expectedOutcome: 'Result', reversalMethod: 'Reset', expiresAt: '2039-01-01T00:00:00Z', createdAt: task.createdAt } as Approval
let store: ReturnType<typeof useAppStore>
beforeEach(() => {
  store = { tasks: [task, { ...task, id: 'task-10', title: 'Other request' }], selectedTaskId: task.id, agents: [], artifacts: [], approvals: [approval, { ...approval, id: 'foreign', taskId: 'task-10', title: 'Foreign task-1 decision' }], auditEvents: [], system: { emergencyStop: false }, connection: 'connected', error: null, loading: false, resyncRequired: false, lastSync: 'initial', runtime: { identities: [] }, decomposition: { record: null, error: '' }, coordination: { record: null, error: '' }, action: vi.fn().mockResolvedValue({}), refresh: vi.fn(), selectTask: vi.fn() } as unknown as typeof store
  vi.mocked(useAppStore).mockImplementation(() => store)
})

test('embedded approvals remain scoped when task IDs and free text overlap or search is cleared', async () => {
  render(<MemoryRouter><TaskDetails/></MemoryRouter>)
  await userEvent.click(screen.getByRole('button', { name: 'Approvals (1 pending)' }))
  await screen.findByRole('article', { name: 'Own decision' })
  expect(screen.queryByRole('article', { name: 'Foreign task-1 decision' })).not.toBeInTheDocument()
  const search = screen.getByRole('searchbox')
  await userEvent.clear(search)
  expect(screen.queryByRole('article', { name: 'Foreign task-1 decision' })).not.toBeInTheDocument()
  await userEvent.type(search, 'foreign')
  expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  await userEvent.clear(search)
  await userEvent.click(within(screen.getByRole('article', { name: 'Own decision' })).getByRole('button', { name: 'Approve' }))
  expect(store.action).toHaveBeenCalledExactlyOnceWith('/api/approvals/own/approve', { decisionNote: null })
})

test('timeline retains the latest 20 sequences despite regressing timestamps without mutating store order', () => {
  store.auditEvents = Array.from({ length: 21 }, (_, index) => ({ id: `event-${index + 1}`, taskId: task.id, sequenceNumber: index + 1, summary: `Evidence ${index + 1}`, timestamp: new Date(Date.UTC(2026, 9, 12, 12, 21 - index)).toISOString(), correlationId: 'request' }) as AuditEvent).reverse()
  store.auditEvents.push({ ...store.auditEvents[0]!, id: 'foreign-event', taskId: 'task-10', sequenceNumber: 99, summary: 'Foreign evidence' })
  const original = [...store.auditEvents]
  render(<MemoryRouter><TaskDetails/></MemoryRouter>)
  const rows = within(screen.getByRole('list')).getAllByRole('listitem')
  expect(rows).toHaveLength(20)
  expect(rows[0]).toHaveTextContent('Evidence 21')
  expect(rows[19]).toHaveTextContent('Evidence 2')
  expect(rows.map(row => within(row).getByText(/^Evidence/).textContent)).toEqual(Array.from({ length: 20 }, (_, index) => `Evidence ${21 - index}`))
  expect(screen.queryByText('Evidence 1')).not.toBeInTheDocument()
  expect(screen.queryByText('Foreign evidence')).not.toBeInTheDocument()
  expect(store.auditEvents).toEqual(original)
})
