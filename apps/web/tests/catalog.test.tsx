import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { AgentCatalog } from '../src/components/AgentCatalog'
import { useCatalogState } from '../src/state/useCatalogState'

vi.mock('../src/state/AppStore', () => ({ useAppStore: () => ({ catalog: useCatalogState(async () => {}) }) }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const entry = { id: 'catalog-one', kind: 'agent', display_name: 'Python Pro', role: 'python-pro', capabilities: ['software.python'], source_repository: 'wshobson/agents', source_commit: 'a'.repeat(40), source_path: 'plugins/python-development/agents/python-pro.md', source_license: 'MIT', revision_id: 'revision-one', review_status: 'unreviewed', enabled: false, trust_status: 'external_untrusted', duplicate_of: null, identity_id: null, warnings: [] }
const detail = { ...entry, original_definition: '<script>untrusted source</script>', source_hash: 'b'.repeat(64), parser_version: '1', license_text: 'MIT fixture', normalized: { requested_tool_classes: ['shell.execute'], unmapped_tags: [], applicable_agent_classes: [], references: [] } }
const reply = (data: unknown) => new Response(JSON.stringify({ data, meta: { schemaVersion: '1.0' } }), { status: 200 })

test('review submits exact revision and never activates during browsing or approval', async () => {
  const mutations: { path: string; body: unknown }[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input))
    if (init?.method === 'POST') { mutations.push({ path: url.pathname, body: JSON.parse(String(init.body)) }); return reply(detail) }
    if (url.pathname.endsWith('/catalog-one')) return reply(detail)
    return reply({ items: [entry], total: 202, offset: Number(url.searchParams.get('offset')), limit: 25 })
  })
  render(<AgentCatalog />)
  expect(globalThis.fetch).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Agent Catalog' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Inspect Python Pro' }))
  expect(await screen.findByRole('button', { name: 'Activate reviewed specialist' })).toBeDisabled()
  expect(screen.getByText('<script>untrusted source</script>')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Review reason'), 'Read and reviewed this revision')
  await userEvent.click(screen.getByRole('button', { name: 'Approve exact revision' }))
  await waitFor(() => expect(mutations).toHaveLength(1))
  expect(mutations[0]).toEqual({ path: '/api/catalog/entries/catalog-one/review', body: { revision_id: 'revision-one', approved: true, reason: 'Read and reviewed this revision' } })
})

test('large catalogs request a bounded page and active workforce filters on the server', async () => {
  const paths: string[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input)); paths.push(url.search)
    return reply({ items: [entry], total: 220, offset: Number(url.searchParams.get('offset')), limit: 25 })
  })
  render(<AgentCatalog />)
  await userEvent.click(screen.getByRole('button', { name: 'Agent Catalog' }))
  await screen.findByRole('button', { name: 'Inspect Python Pro' })
  await userEvent.click(screen.getByRole('button', { name: 'Next catalog page' }))
  await waitFor(() => expect(paths).toHaveLength(2))
  expect(paths[1]).toContain('offset=25&limit=25')
  await userEvent.click(screen.getByRole('button', { name: 'Active catalog workforce' }))
  await waitFor(() => expect(paths).toHaveLength(3))
  expect(paths[2]).toContain('kind=agent&active_only=true')
})
