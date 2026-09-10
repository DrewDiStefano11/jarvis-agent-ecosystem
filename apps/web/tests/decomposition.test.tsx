import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { PlannedWork } from '../src/components/PlannedWork'
import { useDecompositionState } from '../src/state/useDecompositionState'
import type { Decomposition } from '../src/types/decomposition'

const record: Decomposition = {
  id: 'dec-one', taskId: 'task-one', version: 1, status: 'ready', teamSelectionId: 'selection-one',
  objectiveSummary: 'Assess a business.', issues: [],
  subtasks: [{ id: 'sub-one', key: 'research', title: 'Compare markets', description: 'Compare five markets.',
    assignedAgentId: 'specialist-one', assignedAgentName: 'Market Researcher', assignmentRationale: 'Covers research.market',
    requiredCapabilities: ['research.market'], dependsOn: [], deliverable: 'Market comparison', outputType: 'analysis',
    completionCriteria: ['Include five sourced comparisons.'], order: 0, status: 'ready' },
  { id: 'sub-two', key: 'financial', title: 'Analyze economics', description: 'Use market evidence.',
    assignedAgentId: 'specialist-two', assignedAgentName: 'Financial Analyst', assignmentRationale: 'Covers financial analysis',
    requiredCapabilities: ['business.financial-analysis'], dependsOn: ['research'], deliverable: 'Financial model', outputType: 'analysis',
    completionCriteria: ['Include revenue and cost estimates.'], order: 1, status: 'blocked' }],
}

afterEach(() => vi.unstubAllGlobals())

test('shows planned owners, dependency outputs, criteria and explicit blocked state', () => {
  render(<PlannedWork record={record} error="" />)
  expect(screen.getByText(/Market Researcher/)).toBeInTheDocument()
  expect(screen.getByText(/Depends on: Compare markets/)).toBeInTheDocument()
  expect(screen.getByText('Analyze economics · blocked')).toBeInTheDocument()
  expect(screen.getByText('Include revenue and cost estimates.')).toBeInTheDocument()
  expect(screen.getByText(/Specialists have not executed/)).toBeInTheDocument()
})

test('reload reconstructs backend graph and stale requests cannot replace a new task', async () => {
  let release: (value: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('old-task') ? new Promise<Response>(resolve => { release = resolve }) : Promise.resolve(new Response(JSON.stringify({ data: record })))))
  function View({ taskId }: { taskId: string }) {
    const state = useDecompositionState(taskId, null)
    return <PlannedWork record={state.record} error={state.error} />
  }
  const first = render(<View taskId="old-task" />)
  first.rerender(<View taskId="task-one" />)
  await screen.findByText('Version 1 · ready')
  await act(async () => { release(new Response(JSON.stringify({ data: { ...record, version: 99 } }))) })
  expect(screen.queryByText('Version 99 · ready')).not.toBeInTheDocument()
  first.unmount()
  render(<View taskId="task-one" />)
  await screen.findByText('Version 1 · ready')
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
})
