import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { Agents } from '../src/pages/Agents'
import { AppStoreProvider, useAppStore } from '../src/state/AppStore'
import type { Agent, Department } from '../src/types/contracts'

const now = '2026-04-02T15:00:00Z'
function agent(patch: Partial<Agent> & Pick<Agent, 'id' | 'name'>): Agent {
  return {
    schemaVersion: '1.0', role: 'Specialist', description: `${patch.name} simulated description`, goals: [], departmentId: 'research', managerId: null,
    status: 'idle', currentTaskId: null, queuedTaskIds: [], progress: 10, statusMessage: 'Available', capabilities: ['simulation'], allowedTools: [], deniedTools: [],
    approvalPolicy: {}, memoryAccess: {}, performance: { completionRate: 0.9, accuracyScore: 0.9, averageCompletionTime: 10, failedTaskCount: 0, userCorrectionCount: 0, reviewerScore: 0.9, reliabilityScore: 0.95 },
    resourceProfile: {}, office: { zone: 'Research', deskId: 'D', spriteIdentifier: patch.id, displayPosition: { x: 1, y: 1 }, currentAnimationState: 'idle', currentDestination: null, isInMeeting: false },
    createdAt: now, updatedAt: now, version: '1', deploymentStatus: 'simulated', isTemporary: false, ...patch,
  }
}

const departments: Department[] = [{ id: 'research', name: 'Research', description: '', managerAgentId: null, agentIds: ['scout'] }, { id: 'executive', name: 'Executive', description: '', managerAgentId: 'jarvis', agentIds: ['jarvis'] }]
let agents: Agent[]
class Socket { static OPEN = 1; readyState = 1; onopen: (() => void) | null = null; onclose: (() => void) | null = null; constructor() { queueMicrotask(() => this.onopen?.()) } close() {} send() {} }

function Selection() {
  const { selectedAgentId } = useAppStore()
  return <output aria-label="Selected agent">{selectedAgentId ?? 'none'}</output>
}

function renderAgents() {
  window.history.pushState({}, '', '/agents')
  return render(<BrowserRouter><AppStoreProvider><Routes><Route path="/agents" element={<><Agents /><Selection /></>} /></Routes></AppStoreProvider></BrowserRouter>)
}

beforeEach(() => {
  agents = [
    agent({ id: 'jarvis', name: 'Jarvis', role: 'Executive Manager', departmentId: 'executive', status: 'idle' }),
    agent({ id: 'scout', name: 'Scout', status: 'researching', description: 'Reads fixture sources', isTemporary: true, capabilities: ['fixtures'] }),
  ]
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
    if (url.pathname === '/api/agents/temporary' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { name: string; role: string; departmentId: string }
      agents = [...agents, agent({ id: 'temp-1', name: String(body.name), role: String(body.role), departmentId: body.departmentId, isTemporary: true })]
      return { ok: true, status: 201, json: async () => ({ data: { id: 'temp-1' } }) } as Response
    }
    const data = url.pathname === '/api/agents' ? agents : url.pathname === '/api/departments' ? departments : url.pathname === '/api/identity/agents' ? [] : url.pathname === '/api/system/status' ? { eventSessionId: 'agents-test' } : []
    return { ok: true, status: 200, json: async () => ({ data }) } as Response
  }))
})

describe('Agents workforce page', () => {
  test('separates runtime identities, catalog definitions, and demonstration agents', async () => {
    renderAgents()
    expect(await screen.findByRole('heading', { name: 'Jarvis' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Runtime identities' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Catalog boundary' })).toBeInTheDocument()
    expect(screen.getByText('Five permanent operators')).toBeInTheDocument()
    const scout = screen.getByRole('article', { name: 'Scout demonstration' })
    expect(within(scout).getByText(/not a runtime identity/)).toBeInTheDocument()
    expect(within(scout).getByText(/Simulated reliability 95%/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open Jarvis' })).toBeInTheDocument()
  })

  test('filters demonstration agents without registering an identity', async () => {
    renderAgents()
    await screen.findByRole('heading', { name: 'Jarvis' })
    await userEvent.type(screen.getByLabelText('Find a demonstration agent'), 'fixture')
    expect(screen.getByRole('heading', { name: 'Scout' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Jarvis' })).not.toBeInTheDocument()
    expect(screen.getByText(/Showing 1 of 2 demonstration agents/)).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Find a demonstration agent'))
    await userEvent.selectOptions(screen.getByLabelText('Demonstration record'), 'permanent')
    expect(screen.getByRole('heading', { name: 'Jarvis' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Scout' })).not.toBeInTheDocument()
    expect(screen.queryByText(/No demonstration agents match this filter/)).not.toBeInTheDocument()
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  test('opens the shared agent detail and explains temporary creation', async () => {
    renderAgents()
    await userEvent.click(await screen.findByRole('button', { name: 'Open Scout' }))
    expect(screen.getByLabelText('Selected agent')).toHaveTextContent('scout')
    await userEvent.click(screen.getByRole('button', { name: 'Create temporary agent' }))
    const form = within(screen.getByRole('form', { name: 'Create demonstration agent' }))
    expect(form.getByText(/does not register a runtime identity/)).toBeInTheDocument()
    await userEvent.type(form.getByLabelText('Name'), 'Temp Scout')
    await userEvent.type(form.getByLabelText('Role'), 'Notes')
    await userEvent.click(form.getByRole('button', { name: 'Create restricted simulation' }))
    expect(await screen.findByText(/Temporary demonstration agent saved/)).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Temp Scout' })).toBeInTheDocument()
    const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(String(posts[0]?.[0])).toContain('/api/agents/temporary')
  })
})
