import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import { ToolExecutionPanel } from '../src/components/ToolExecutionPanel'
import { toolAuthorizationBody } from '../src/state/useToolExecutionState'
import type { ModelExecution } from '../src/types/runtime'
import type { ToolScope } from '../src/types/toolExecution'

const store = vi.hoisted(() => ({
  tools: { workspaces: [{ workspaceId: 'lab', displayName: 'Lab reports', ready: true, allowedTools: ['workspace.report'], readPrefixes: ['inputs'], writePrefixes: ['reports'] }], executions: [], artifact: null, error: null, loading: false, authorize: vi.fn(), refreshTools: vi.fn(), openArtifact: vi.fn() },
  system: { emergencyStop: false }, refresh: vi.fn(), selectTask: vi.fn(),
}))
vi.mock('../src/state/AppStore', () => ({ useAppStore: () => store }))
const execution = { executionId: 'model-result', resultHash: 'a'.repeat(64), result: { steps: [{ tool: 'workspace.report', path: 'reports/objective.md', content: '# Supplied facts\nA practical plan.' }] } } as ModelExecution
beforeEach(() => { vi.clearAllMocks(); store.system.emergencyStop = false; store.tools.authorize.mockResolvedValue({ executionId: 'tools-one' }) })

test('file execution requires explicit scope selection and acknowledgement of exact content', async () => {
  render(<ToolExecutionPanel execution={execution}/>)
  const button = screen.getByRole('button', { name: 'Authorize workspace execution' })
  expect(button).toBeDisabled()
  await userEvent.click(screen.getByText('Inspect exact file content'))
  expect(screen.getByText(/A practical plan/)).toBeVisible()
  await userEvent.selectOptions(screen.getByLabelText('Authorized workspace'), 'lab')
  expect(button).toBeDisabled()
  expect(store.tools.authorize).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(button)
  expect(store.tools.authorize).toHaveBeenCalledWith('model-result', 'a'.repeat(64), {
    workspaceId: 'lab', allowedTools: ['workspace.report'], readPrefixes: ['inputs'], writePrefixes: ['reports'], maximumBytes: 65536, maximumSteps: 8,
  })
  expect(await screen.findByRole('status')).toHaveTextContent('Authorized execution tools-one')
})

test('emergency stop blocks authorization and changing workspace resets acknowledgement', async () => {
  store.system.emergencyStop = true
  render(<ToolExecutionPanel execution={execution}/>)
  await userEvent.selectOptions(screen.getByLabelText('Authorized workspace'), 'lab')
  await userEvent.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: 'Authorize workspace execution' })).toBeDisabled()
  await userEvent.selectOptions(screen.getByLabelText('Authorized workspace'), '')
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(store.tools.authorize).not.toHaveBeenCalled()
})

test('ambiguous authorization retry stays stable across reloads and separates actors or scopes', async () => {
  const scope: ToolScope = { workspaceId: 'lab', allowedTools: ['workspace.report'], readPrefixes: ['inputs'], writePrefixes: ['reports'], maximumBytes: 65536, maximumSteps: 8 }
  const first = await toolAuthorizationBody('actor', 'source', 'a'.repeat(64), scope)
  expect(await toolAuthorizationBody('actor', 'source', 'a'.repeat(64), structuredClone(scope))).toEqual(first)
  expect((await toolAuthorizationBody('different', 'source', 'a'.repeat(64), scope)).commandId).not.toBe(first.commandId)
  expect((await toolAuthorizationBody('actor', 'source', 'a'.repeat(64), { ...scope, workspaceId: 'another' })).commandId).not.toBe(first.commandId)
  expect(within(document.body).queryByText('# Supplied facts')).toBeNull()
})
