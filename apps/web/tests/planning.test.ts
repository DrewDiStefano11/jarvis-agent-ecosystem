import { beforeEach, expect, test, vi } from 'vitest'
import { newPlanningSubmission, submitPlanning } from '../src/api/planning'
import { request } from '../src/api/client'
import type { Task } from '../src/types/contracts'

vi.mock('../src/api/client', () => ({ request: vi.fn() }))
const task = { id: 'task-test', description: 'Plan the weekend.', projectId: null } as Task

beforeEach(() => {
  vi.mocked(request).mockReset()
  vi.mocked(request).mockImplementation(async path => path.includes('/context/')
    ? { id: 'assembly-test', status: 'completed' }
    : { snapshot: { specification: { run_id: 'run-test' }, version: 1 } })
})

test.each([true, false])('planning replays captured output format without upgrading old submissions: %s', async current => {
  const submission = newPlanningSubmission(task, 'actor-test', 'actor-test')
  if (!current) delete submission.responseFormat
  const captured = JSON.parse(JSON.stringify(submission))
  await submitPlanning(captured)
  await submitPlanning(captured)
  const calls = vi.mocked(request).mock.calls
  expect(calls.slice(0, 3)).toEqual(calls.slice(3, 6))
  const command = JSON.parse(calls[1]![1]!.body as string)
  expect(command.specification.autonomous_execution.response_format).toBe(current ? 'planning_review_json_v1' : undefined)
})
