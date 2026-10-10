import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { BusinessLab } from '../src/pages/BusinessLab'
import { AppStoreProvider, useAppStore } from '../src/state/AppStore'
import type { Task } from '../src/types/contracts'

const now = '2026-04-02T15:00:00Z'
function task(patch: Partial<Task> & Pick<Task, 'id' | 'title' | 'status' | 'updatedAt'>): Task {
  return {
    schemaVersion: '1.0', description: patch.description ?? 'Recorded objective description', request: patch.title,
    parentTaskId: null, childTaskIds: [], projectId: 'business-lab', createdBy: 'user', assignedManagerId: null,
    assignedAgentIds: patch.assignedAgentIds ?? ['identity-analyst'], priority: 'high', progress: 20, statusMessage: 'Stored status',
    dependencies: [], blockedBy: [], approvalIds: [], artifactIds: patch.artifactIds ?? ['artifact-1'], result: null, error: null,
    retryCount: 0, maxRetries: 1, createdAt: now, startedAt: null, completedAt: null, ...patch,
  }
}

let tasks: Task[]
let failTasks = false
class Socket { static OPEN = 1; readyState = 1; onopen: (() => void) | null = null; onclose: (() => void) | null = null; constructor() { queueMicrotask(() => this.onopen?.()) } close() {} send() {} }

function SelectedTask() {
  const { selectedTaskId } = useAppStore()
  return <output aria-label="Selected task">{selectedTaskId ?? 'none'}</output>
}

function renderLab() {
  window.history.pushState({}, '', '/lab')
  return render(<BrowserRouter><AppStoreProvider><Routes>
    <Route path="/lab" element={<><BusinessLab /><SelectedTask /></>} />
    <Route path="/runtime" element={<h1>Planning workspace</h1>} />
  </Routes></AppStoreProvider></BrowserRouter>)
}

beforeEach(() => {
  tasks = [
    task({ id: 'obj-queued', title: 'Customer interview synthesis', description: 'Summarize recorded interviews', status: 'queued', updatedAt: '2026-04-01T12:00:00Z', result: null }),
    task({ id: 'obj-alpha', title: 'Alpha notes', description: 'Earlier naming pass', status: 'queued', updatedAt: '2026-04-04T12:00:00Z', assignedAgentIds: [], artifactIds: [] }),
    task({ id: 'obj-plan', title: 'Pricing worksheet', description: 'Compare existing offers', status: 'planning', updatedAt: '2026-04-03T12:00:00Z', assignedAgentIds: ['scout'], progress: 10 }),
    task({ id: 'obj-done', title: 'Launch retrospective', description: 'Closed report', status: 'completed', updatedAt: '2026-03-01T12:00:00Z', result: 'Recorded retrospective text', progress: 100, artifactIds: [] }),
  ]
  failTasks = false
  localStorage.clear()
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
    if (url.pathname === '/api/tasks' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Partial<Task>
      const created = task({ id: 'obj-new', title: body.title ?? 'New', description: body.description, status: 'queued', updatedAt: now, projectId: body.projectId ?? null, assignedAgentIds: [], artifactIds: [], priority: body.priority ?? 'medium' })
      tasks = [...tasks, created]
      return { ok: true, status: 201, json: async () => ({ data: created }) } as Response
    }
    if (url.pathname === '/api/tasks') {
      if (failTasks) throw new Error('Objectives unavailable')
      return { ok: true, status: 200, json: async () => ({ data: tasks }) } as Response
    }
    if (url.pathname === '/api/identity/agents') return { ok: true, status: 200, json: async () => ({ data: [{ id: 'identity-analyst', display_name: 'Analyst', stable_key: 'analyst', description: '', agent_type: 'worker', is_enabled: true, lifecycle_state: 'active', operational_status: 'idle', version: 1 }] }) } as Response
    if (url.pathname === '/api/agents') return { ok: true, status: 200, json: async () => ({ data: [{ id: 'scout', name: 'Scout', role: 'Research', isTemporary: false }] }) } as Response
    if (url.pathname === '/api/system/status') return { ok: true, status: 200, json: async () => ({ data: { eventSessionId: 'lab-test' } }) } as Response
    return { ok: true, status: 200, json: async () => ({ data: [] }) } as Response
  }))
})

describe('Business Lab workspace', () => {
  test('shows only lab objectives, their recorded stage, and distinct participant kinds', async () => {
    renderLab()
    expect(await screen.findByRole('heading', { name: 'Customer interview synthesis' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Pricing worksheet' })).toBeInTheDocument()
    expect(screen.queryByText('Other project')).not.toBeInTheDocument()
    const queued = screen.getByRole('region', { name: /Queued/ })
    expect(within(queued).getByText(/Analyst \(runtime identity\)/)).toBeInTheDocument()
    expect(within(queued).getAllByText(/does not queue a plan/).length).toBeGreaterThan(0)
    const planning = screen.getByRole('region', { name: /Planning/ })
    expect(within(planning).getByText(/Scout \(demonstration agent\)/)).toBeInTheDocument()
    expect(screen.getByText('Recorded retrospective text')).toBeInTheDocument()
    expect(screen.getByText('Result recorded')).toBeInTheDocument()
  })

  test('searches, filters by recorded status, and sorts by title without writing', async () => {
    renderLab()
    await screen.findByRole('heading', { name: 'Customer interview synthesis' })
    const postsBefore = () => vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(postsBefore()).toHaveLength(0)
    await userEvent.type(screen.getByLabelText('Search objectives'), 'pricing')
    expect(screen.getByRole('heading', { name: 'Pricing worksheet' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Customer interview synthesis' })).not.toBeInTheDocument()
    expect(screen.getByText(/Showing 1 of 4 objectives/)).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Search objectives'))
    await userEvent.selectOptions(screen.getByLabelText('Recorded status'), 'completed')
    expect(screen.getByRole('heading', { name: 'Launch retrospective' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Pricing worksheet' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await userEvent.selectOptions(screen.getByLabelText('Sort objectives'), 'title')
    const queued = screen.getByRole('region', { name: /Queued/ })
    expect(within(queued).getAllByRole('heading', { level: 3 }).map(item => item.textContent)).toEqual(['Alpha notes', 'Customer interview synthesis'])
    expect(postsBefore()).toHaveLength(0)
  })

  test('stage filters are keyboard reachable and explain an empty match', async () => {
    renderLab()
    await screen.findByRole('heading', { name: 'Customer interview synthesis' })
    const attention = screen.getByRole('button', { name: /Needs attention/ })
    attention.focus()
    expect(attention).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(attention).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/No objectives match this search or filter/)).toBeInTheDocument()
  })

  test('opens task history from the objective and does not imply execution started', async () => {
    renderLab()
    const card = within((await screen.findByRole('heading', { name: 'Customer interview synthesis' })).closest('article')!)
    await userEvent.click(card.getByRole('button', { name: 'Inspect task history' }))
    expect(screen.getByLabelText('Selected task')).toHaveTextContent('obj-queued')
  })

  test('confirms a saved objective is still queued', async () => {
    renderLab()
    await screen.findByRole('heading', { name: 'Customer interview synthesis' })
    await userEvent.click(screen.getByRole('button', { name: 'New objective' }))
    await userEvent.type(screen.getByLabelText('Title'), 'New market note')
    await userEvent.type(screen.getByLabelText('Description'), 'Capture the facts we already have.')
    await userEvent.click(screen.getByRole('button', { name: 'Create task' }))
    const status = await screen.findByText(/Objective saved and still queued/)
    expect(status).toHaveTextContent('still queued')
    expect(status).not.toHaveTextContent(/execution has started|planning has started|autonomous/i)
    expect(await screen.findByRole('heading', { name: 'New market note' })).toBeInTheDocument()
    const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(String(posts[0]?.[0])).toContain('/api/tasks')
    expect(JSON.parse(String(posts[0]?.[1]?.body))).toMatchObject({ projectId: 'business-lab', title: 'New market note' })
  })

  test('shows an empty workspace and an unavailable objective list', async () => {
    tasks = []
    renderLab()
    expect(await screen.findByText(/Create your first objective/)).toBeInTheDocument()
  })

  test('keeps an API failure visible instead of inventing objectives', async () => {
    failTasks = true
    renderLab()
    expect(await screen.findByRole('alert')).toHaveTextContent('Objectives unavailable')
    expect(screen.queryByRole('heading', { name: 'Customer interview synthesis' })).not.toBeInTheDocument()
  })
})
