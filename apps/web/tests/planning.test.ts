import { beforeEach, expect, test, vi } from 'vitest'
import { newPlanningSubmission, submitPlanning } from '../src/api/planning'
import { request } from '../src/api/client'
import type { Task } from '../src/types/contracts'

vi.mock('../src/api/client', () => ({ request: vi.fn() }))
const task = { id: 'task-test', description: 'Plan the weekend.', projectId: null } as Task

beforeEach(() => {
  vi.mocked(request).mockReset()
  vi.mocked(request).mockImplementation(async path => path.includes('/context/')
    ? { id: 'assembly-test', status: 'completed', manifest: { includedSources: [{ sourceType: 'operator_instruction' }] } }
    : { snapshot: { specification: { run_id: 'run-test' }, version: 1 } })
})

test('workspace proposals retain their explicit mode and never invoke tools while planning', async () => {
  const submission = newPlanningSubmission(task, 'actor-test', 'actor-test', 'workspace')
  await submitPlanning(submission)
  const calls = vi.mocked(request).mock.calls
  expect(calls).toHaveLength(3)
  const context = JSON.parse(calls[0]![1]!.body as string)
  expect(Object.values(context.toolAvailabilitySummary).every(value => Array.isArray(value) && value.every(item => typeof item === 'string'))).toBe(true)
  const command = JSON.parse(calls[1]![1]!.body as string)
  expect(command.specification.autonomous_execution.execution_type).toBe('workspace_plan')
  expect(command.specification.autonomous_execution.response_format).toBe('workspace_plan_json_v1')
  expect(calls.some(([path]) => path.includes('tool-executions'))).toBe(false)
})

test.each([true, false])('planning replays captured output format without upgrading old submissions: %s', async current => {
  const submission = newPlanningSubmission(task, 'actor-test', 'actor-test')
  if (!current) { delete submission.responseFormat; delete submission.budgetProfile }
  const captured = JSON.parse(JSON.stringify(submission))
  await submitPlanning(captured)
  await submitPlanning(captured)
  const calls = vi.mocked(request).mock.calls
  expect(calls.slice(0, 3)).toEqual(calls.slice(3, 6))
  const command = JSON.parse(calls[1]![1]!.body as string)
  expect(command.specification.autonomous_execution.response_format).toBe(current ? 'planning_review_json_v1' : undefined)
})

test('submitPlanning sends the selected planning actor ID in X-Jarvis-Actor-Id header for context assembly', async () => {
  const submission = newPlanningSubmission(task, 'actor-12345', 'target-test')
  await submitPlanning(submission)

  const calls = vi.mocked(request).mock.calls
  expect(calls).toHaveLength(3)

  // First call is to /api/context/assemblies
  const contextCall = calls[0]!
  expect(contextCall[0]).toBe('/api/context/assemblies')
  expect(contextCall[1]!.headers).toMatchObject({
    'Idempotency-Key': `planning-context-${submission.id}`,
    'X-Jarvis-Actor-Id': 'actor-12345'
  })
})

test('new planning captures one direct request and a persisted aggregate budget', async () => {
  const submission = newPlanningSubmission(task, 'actor-test', 'actor-test')
  await submitPlanning(submission)
  const calls = vi.mocked(request).mock.calls
  const context = JSON.parse(calls[0]![1]!.body as string)
  expect(context.executionBudget).toMatchObject({ maximumRequests: 1, maximumTokens: 7500, maximumAttempts: 1 })
  expect(context.executionBudget.stages).toEqual([{ stage: 'planning', maximumRequests: 1, maximumInputTokens: 5964, maximumOutputTokens: 1536 }])
  const command = JSON.parse(calls[1]![1]!.body as string)
  expect(command.specification.maximum_permitted_attempts).toBe(1)
  expect(command.specification.autonomous_execution).toMatchObject({ maximum_provider_requests: 1, maximum_repair_calls: 0, maximum_output_tokens: 1536 })
})

test('an inconsistent captured direct profile is rejected before any HTTP command', async () => {
  const submission = newPlanningSubmission(task, 'actor-test', 'actor-test')
  delete submission.responseFormat
  await expect(submitPlanning(submission)).rejects.toThrow(/profile|format/i)
  expect(request).not.toHaveBeenCalled()
})
