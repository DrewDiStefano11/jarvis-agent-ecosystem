import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, test, vi } from 'vitest'
import { ToolExecutionHistory, ToolExecutionPanel } from '../src/components/ToolExecutionPanel'
import { toolAuthorizationBody, useToolExecutionState } from '../src/state/useToolExecutionState'
import { request } from '../src/api/client'
import type { ModelExecution } from '../src/types/runtime'
import type { ToolExecution, ToolScope } from '../src/types/toolExecution'

const store = vi.hoisted(() => ({
  tools: { workspaces: [{ workspaceId: 'lab', displayName: 'Lab reports', ready: true, allowedTools: ['workspace.report'], readPrefixes: ['inputs'], writePrefixes: ['reports'] }], executions: [] as ToolExecution[], artifact: null, error: null, loading: false, authorize: vi.fn(), refreshTools: vi.fn(), openArtifact: vi.fn() },
  system: { emergencyStop: false }, refresh: vi.fn(), selectTask: vi.fn(),
}))
const connected = vi.hoisted(() => ({ current: null as object | null }))
vi.mock('../src/state/AppStore', () => ({ useAppStore: () => connected.current ?? store }))
vi.mock('../src/api/client', () => ({ request: vi.fn() }))
const execution = { executionId: 'model-result', stage: 'completed', resultHash: 'a'.repeat(64), result: { steps: [{ tool: 'workspace.report', path: 'reports/objective.md', content: '# Supplied facts\nA practical plan.' }] } } as ModelExecution
beforeEach(() => { vi.clearAllMocks(); connected.current = null; store.tools.executions = []; store.tools.workspaces[0]!.writePrefixes = ['reports']; store.system.emergencyStop = false; store.tools.authorize.mockResolvedValue({ executionId: 'tools-one' }) })

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

test('execution task history exposes its artifacts without needing a model result on that task', async () => {
  store.tools.executions = [{ executionId: 'tools-one', taskId: 'execution-task', stage: 'completed', steps: [], artifacts: [{ artifactId: 'report-one', relativePath: 'reports/report.md', byteCount: 40, contentHash: 'b'.repeat(64) }] } as unknown as ToolExecution]
  render(<ToolExecutionHistory/>)
  expect(screen.getByRole('heading', { name: 'Workspace execution history' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Read reports/report.md' }))
  expect(store.tools.openArtifact).toHaveBeenCalledWith('report-one')
  await userEvent.click(screen.getByRole('button', { name: 'Open execution task' }))
  expect(store.selectTask).toHaveBeenCalledWith('execution-task')
})

test('workspace configuration changes require acknowledgement of the new exact scope', async () => {
  const { rerender } = render(<ToolExecutionPanel execution={execution}/>)
  await userEvent.selectOptions(screen.getByLabelText('Authorized workspace'), 'lab')
  await userEvent.click(screen.getByRole('checkbox'))
  expect(screen.getByRole('button', { name: 'Authorize workspace execution' })).toBeEnabled()
  store.tools.workspaces[0]!.writePrefixes = ['reports', 'other-reports']
  rerender(<ToolExecutionPanel execution={execution}/>)
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Authorize workspace execution' })).toBeDisabled()
  expect(store.tools.authorize).not.toHaveBeenCalled()
})

test('reload resumes a persisted preparing authorization with its original scope and command', async () => {
  const durable = new Map<string, ToolExecution>()
  const submissions: Awaited<ReturnType<typeof toolAuthorizationBody>>[] = []
  vi.mocked(request).mockImplementation(async (path, options) => {
    if (path === '/api/tool-workspaces') return structuredClone(store.tools.workspaces)
    if (path.startsWith('/api/tool-executions?')) return structuredClone([...durable.values()])
    if (path === '/api/tool-executions/authorize') {
      const body = JSON.parse(options!.body as string) as Awaited<ReturnType<typeof toolAuthorizationBody>>
      submissions.push(body)
      const existing = durable.get(body.commandId)
      if (existing) {
        existing.stage = 'queued'
        return structuredClone(existing)
      }
      // The API committed its intent, then stopped before preparing the runtime.
      durable.set(body.commandId, {
        executionId: 'tools-one', sourceExecutionId: body.sourceExecutionId,
        planHash: body.expectedPlanHash, scope: body.scope, stage: 'preparing',
        steps: [], artifacts: [],
      } as unknown as ToolExecution)
      throw new Error('Connection lost after authorization was saved')
    }
    throw new Error(`Unexpected request ${path}`)
  })
  function ConnectedPanel() {
    const tools = useToolExecutionState('actor', 'source-task', null)
    connected.current = { ...store, tools }
    return <ToolExecutionPanel execution={execution}/>
  }
  const first = render(<ConnectedPanel/>)
  await screen.findByRole('option', { name: 'Lab reports' })
  await userEvent.selectOptions(screen.getByLabelText('Authorized workspace'), 'lab')
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(screen.getByRole('button', { name: 'Authorize workspace execution' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Connection lost')
  expect(durable.size).toBe(1)
  first.unmount()

  // A changed workspace marker must not silently widen the original request.
  store.tools.workspaces[0]!.writePrefixes = ['reports', 'other-reports']
  const reloaded = render(<ConnectedPanel/>)
  const resume = await screen.findByRole('button', { name: 'Resume workspace authorization' })
  expect(screen.getByLabelText('Authorized workspace')).toHaveValue('lab')
  expect(screen.getByLabelText('Authorized workspace')).toBeDisabled()
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(resume).toBeDisabled()
  expect(screen.queryByText(/write under reports, other-reports/)).toBeNull()
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(resume)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Authorize workspace execution' })).toBeDisabled())
  expect(submissions).toHaveLength(2)
  expect(submissions[1]).toEqual(submissions[0])
  expect(durable.size).toBe(1)
  expect([...durable.values()][0]?.stage).toBe('queued')
  reloaded.unmount()
  connected.current = null
})
