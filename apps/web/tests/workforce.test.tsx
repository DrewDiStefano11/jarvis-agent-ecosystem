import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { Agents } from '../src/pages/Agents'
import { AppStoreProvider, useAppStore } from '../src/state/AppStore'
import type { RuntimeIdentity } from '../src/types/runtime'

const first: RuntimeIdentity = { id: 'identity-first', display_name: 'Researcher', stable_key: 'researcher', description: 'Research registered facts', agent_type: 'worker', is_enabled: true, lifecycle_state: 'provisioned', operational_status: 'offline', version: 1 }
let rows: RuntimeIdentity[]
let failure: 'none' | 'lost-ack' | 'unavailable' | 'temporary' = 'none'
const reply = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => status < 400 ? { data } : data } as Response)
class Socket { static OPEN = 1; static CONNECTING = 0; readyState = 1; onopen: (() => void) | null = null; onclose: (() => void) | null = null; constructor() { queueMicrotask(() => this.onopen?.()) } close() {} send() {} }

function SharedTargets() {
  const { runtime } = useAppStore()
  return <><h1>Planning targets</h1><select aria-label="Shared planning targets">{runtime.identities.filter(row => row.is_enabled && row.lifecycle_state === 'active').map(row => <option key={row.id}>{row.display_name}</option>)}</select><Link to="/agents">Return to Agents</Link></>
}
function renderWorkforce() {
  return render(<BrowserRouter><AppStoreProvider><Routes><Route path="/agents" element={<Agents/>}/><Route path="/runtime" element={<SharedTargets/>}/></Routes></AppStoreProvider></BrowserRouter>)
}
beforeEach(() => {
  rows = [{ ...first }]; failure = 'none'
  window.history.pushState({}, '', '/agents')
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
    const path = url.pathname
    if (path === '/api/identity/capabilities') return reply([{ id: 'cap-research', stable_key: 'research', display_name: 'Research', is_enabled: true }])
    if (path === '/api/identity/agents' && init?.method === 'POST') {
      if (failure === 'unavailable') throw new TypeError('Network unavailable')
      const body = JSON.parse(String(init.body))
      if (rows.some(row => row.stable_key === body.stable_key)) return reply({ error: { code: 'DUPLICATE_STABLE_KEY', message: 'Already registered' } }, 409)
      const row = { ...first, ...body, id: `identity-${rows.length + 1}` }
      rows.push(row)
      if (failure === 'lost-ack') { failure = 'none'; throw new TypeError('Acknowledgement lost') }
      return reply(row, 201)
    }
    if (path === '/api/identity/agents') {
      if (failure === 'unavailable') throw new TypeError('Network unavailable')
      return reply(url.searchParams.has('capability') ? rows.filter(row => row.id === first.id) : [...rows])
    }
    if (path.startsWith('/api/identity/agents/')) {
      const [, , , , id, transition] = path.split('/')
      const row = rows.find(item => item.id === id)!
      if (init?.method === 'PATCH') Object.assign(row, JSON.parse(String(init.body)))
      if (transition) row.lifecycle_state = transition === 'activate' ? 'active' : 'suspended'
      row.version += 1
      return reply({ ...row })
    }
    if (path === '/api/agents/temporary' && failure === 'temporary') return reply({ error: { code: 'UNAVAILABLE', message: 'Demo registration unavailable' } }, 503)
    if (path === '/api/departments') return reply([{ id: 'research', name: 'Research' }])
    if (path === '/api/system/status') return reply({ eventSessionId: 'test' })
    return reply([])
  }))
})

async function fillRegistration(name = 'Analyst', key = 'analyst') {
  await userEvent.click(screen.getByRole('button', { name: 'Register identity' }))
  const form = within(screen.getByRole('form', { name: 'Register identity' }))
  await userEvent.type(form.getByLabelText('Display name'), name)
  await userEvent.type(form.getByLabelText(/Stable key/), key)
  await userEvent.type(form.getByLabelText('Description'), 'Bounded research')
  return form
}

describe('durable workforce', () => {
  test('registration stays provisioned until explicit activation and shares the planning target', async () => {
    renderWorkforce(); await screen.findByRole('article', { name: 'Identity Researcher' })
    const form = await fillRegistration()
    await userEvent.click(form.getByRole('button', { name: 'Register provisioned identity' }))
    const card = within(await screen.findByRole('article', { name: 'Identity Analyst' }))
    expect(card.getByText('provisioned')).toBeInTheDocument()
    const mutationPaths = () => vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST').map(([url]) => String(url))
    expect(mutationPaths()).toEqual(['http://127.0.0.1:8000/api/identity/agents'])
    await userEvent.click(card.getByRole('button', { name: 'Activate identity' }))
    expect(await card.findByText('active')).toBeInTheDocument()
    await userEvent.click(card.getByRole('link', { name: 'Assign work in Planning' }))
    expect(screen.getByLabelText('Shared planning targets')).toHaveTextContent('Analyst')
    expect(mutationPaths().some(path => /permissions|roles|setup/.test(path))).toBe(false)
  })

  test('unknown registration acknowledgement recovers by unique key without duplicate writes', async () => {
    renderWorkforce(); await screen.findByRole('article', { name: 'Identity Researcher' })
    const form = await fillRegistration(); failure = 'lost-ack'
    await userEvent.click(form.getByRole('button', { name: 'Register provisioned identity' }))
    expect(await screen.findByText(/Found the existing registration for Analyst/)).toBeInTheDocument()
    expect(rows.filter(row => row.stable_key === 'analyst')).toHaveLength(1)
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  test('failed registration retains the exact payload and exposes retry', async () => {
    renderWorkforce(); await screen.findByRole('article', { name: 'Identity Researcher' })
    const form = await fillRegistration(); failure = 'unavailable'
    await userEvent.click(form.getByRole('button', { name: 'Register provisioned identity' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable')
    expect(form.getByLabelText(/Stable key/)).toHaveValue('analyst')
    expect(form.getByLabelText(/Stable key/)).toBeDisabled()
    failure = 'none'
    await userEvent.click(form.getByRole('button', { name: 'Retry same registration' }))
    expect(await screen.findByRole('article', { name: 'Identity Analyst' })).toBeInTheDocument()
    const bodies = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST').map(([, init]) => init?.body)
    expect(bodies[0]).toBe(bodies[1])
  })

  test('stable-key conflict never edits an unrelated profile', async () => {
    renderWorkforce(); await screen.findByRole('article', { name: 'Identity Researcher' })
    const form = await fillRegistration('Different profile', 'researcher')
    await userEvent.click(form.getByRole('button', { name: 'Register provisioned identity' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('belongs to a different profile')
    expect(rows).toEqual([first])
  })

  test('profile edits, suspension and enablement update the shared identity', async () => {
    rows[0]!.lifecycle_state = 'active'
    renderWorkforce()
    let card = within(await screen.findByRole('article', { name: 'Identity Researcher' }))
    await userEvent.click(card.getByRole('button', { name: 'Edit profile' }))
    await userEvent.clear(card.getByLabelText('Display name'))
    await userEvent.type(card.getByLabelText('Display name'), 'Senior researcher')
    await userEvent.clear(card.getByLabelText('Description'))
    await userEvent.type(card.getByLabelText('Description'), 'Updated research profile')
    await userEvent.click(card.getByRole('button', { name: 'Save profile' }))
    card = within(await screen.findByRole('article', { name: 'Identity Senior researcher' }))
    expect(card.getByText('Updated research profile')).toBeInTheDocument()
    await userEvent.click(card.getByRole('button', { name: 'Suspend identity' }))
    expect(await card.findByText('suspended')).toBeInTheDocument()
    await userEvent.click(card.getByRole('button', { name: 'Reactivate identity' }))
    await userEvent.click(card.getByRole('button', { name: 'Disable identity' }))
    expect(await card.findByRole('button', { name: 'Enable identity' })).toBeEnabled()
    expect(card.queryByRole('link', { name: 'Assign work in Planning' })).not.toBeInTheDocument()
    await userEvent.click(card.getByRole('button', { name: 'Enable identity' }))
    expect(await card.findByRole('link', { name: 'Assign work in Planning' })).toBeInTheDocument()
  })

  test('capability selection reads effective assignments without replacing the registry', async () => {
    rows.push({ ...first, id: 'identity-other', stable_key: 'other', display_name: 'Other' })
    renderWorkforce(); await screen.findByRole('article', { name: 'Identity Other' })
    await userEvent.selectOptions(screen.getByLabelText('Effective capability'), 'research')
    await waitFor(() => expect(screen.queryByRole('article', { name: 'Identity Other' })).not.toBeInTheDocument())
    expect(await screen.findByText('Effective capability: Research')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Effective capability'), '')
    expect(await screen.findByRole('article', { name: 'Identity Other' })).toBeInTheDocument()
  })

  test('demonstration creation failures remain visible with form values intact', async () => {
    renderWorkforce(); await screen.findByRole('article', { name: 'Identity Researcher' })
    await userEvent.click(screen.getByRole('button', { name: 'Create temporary agent' }))
    const form = within(screen.getByRole('form', { name: 'Create demonstration agent' }))
    await userEvent.type(form.getByLabelText('Name'), 'Demo assistant')
    await userEvent.type(form.getByLabelText('Role'), 'Demo research')
    failure = 'temporary'
    await userEvent.click(form.getByRole('button', { name: 'Create restricted simulation' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Demo registration unavailable')
    expect(form.getByLabelText('Name')).toHaveValue('Demo assistant')
  })

  test('late registry refresh cannot overwrite a successfully activated identity', async () => {
    renderWorkforce(); const card = within(await screen.findByRole('article', { name: 'Identity Researcher' }))
    const initial = vi.mocked(fetch).getMockImplementation()!
    let resolve: (value: Response) => void = () => undefined
    vi.mocked(fetch).mockImplementation((input, init) => String(input).includes('/identity/agents?')
      ? new Promise<Response>(complete => { resolve = complete }) : initial(input, init))
    await userEvent.click(screen.getByRole('button', { name: 'Refresh identities' }))
    await userEvent.click(card.getByRole('button', { name: 'Activate identity' }))
    expect(await card.findByText('active')).toBeInTheDocument()
    await act(async () => resolve(reply([{ ...first }])))
    expect(card.getByText('active')).toBeInTheDocument()
  })
})
