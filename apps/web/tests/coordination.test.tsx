import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { CoordinatedWork } from '../src/components/CoordinatedWork'
import { useCoordinationState } from '../src/state/useCoordinationState'
import type { Coordination } from '../src/types/coordination'

const record: Coordination = {
  id: 'coord-one', taskId: 'task-one', status: 'blocked', blockedReason: 'specialist_suspended',
  nodes: [{ subtaskId: 'sub-one', key: 'research', assignedAgentId: 'specialist-one', status: 'succeeded', attemptCount: 2, retryEligibleAt: null, resultSummary: 'Preserved research', failureDetail: null, provider: 'local', model: 'installed-model' },
    { subtaskId: 'sub-two', key: 'analysis', assignedAgentId: 'specialist-two', status: 'blocked', attemptCount: 0, retryEligibleAt: null, resultSummary: null, failureDetail: 'Upstream blocked', provider: null, model: null }],
  synthesis: { status: 'pending', attemptCount: 0, summary: null, failureDetail: null, retryEligibleAt: null, inputSubtaskIds: [] },
}
afterEach(() => vi.unstubAllGlobals())

test('shows durable failures, preserved work and attempts without inventing completion', () => {
  render(<CoordinatedWork record={record} error="" />)
  expect(screen.getByText('Preserved research')).toBeInTheDocument()
  expect(screen.getByText('analysis · blocked')).toBeInTheDocument()
  expect(screen.getByText(/specialist suspended/)).toBeInTheDocument()
  expect(screen.getByText(/Attempts: 2 \/ 3/)).toBeInTheDocument()
  expect(screen.queryByText('Final result')).not.toBeInTheDocument()
})

test('final result uses the authoritative completed coordinator projection', () => {
  render(<CoordinatedWork record={{ ...record, status: 'completed', blockedReason: null, synthesis: { ...record.synthesis, status: 'succeeded', summary: 'Durable final result', inputSubtaskIds: ['sub-one', 'sub-two'] } }} error="" />)
  expect(screen.getByText('Final result')).toBeInTheDocument()
  expect(screen.getByText('Durable final result')).toBeInTheDocument()
  expect(screen.getByText('2 validated specialist results')).toBeInTheDocument()
})

test('task switching cannot display a stale coordinator response', async () => {
  let release: (value: Response) => void = () => {}
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('old-task') ? new Promise<Response>(resolve => { release = resolve }) : Promise.resolve(new Response(JSON.stringify({ data: record })))))
  function View({ taskId }: { taskId: string }) {
    const state = useCoordinationState(taskId, null)
    return <span>{state.record?.id ?? 'pending'}</span>
  }
  const view = render(<View taskId="old-task" />)
  view.rerender(<View taskId="task-one" />)
  await waitFor(() => expect(screen.getByText('coord-one')).toBeInTheDocument())
  await act(async () => release(new Response(JSON.stringify({ data: { ...record, id: 'stale' } }))))
  expect(screen.queryByText('stale')).not.toBeInTheDocument()
})
