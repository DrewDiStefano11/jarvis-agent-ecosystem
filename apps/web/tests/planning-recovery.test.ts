import { webcrypto } from 'node:crypto'
import { beforeEach, expect, test, vi } from 'vitest'
import type { PlanningSubmission } from '../src/api/planning'
import { forgetPlanningSubmission, readPlanningRecovery, rememberPlanningSubmission, restorePlanningSubmission } from '../src/state/planningRecovery'
import type { Task } from '../src/types/contracts'

const task = { id: 'task-recovery', projectId: null, description: 'Private operator text; never store this in the browser.' } as Task
const submission: PlanningSubmission = { id: 'original-command-id', timestamp: '2026-09-05T12:00:00.000Z',
  actorId: 'actor-worker', targetId: 'target-specialist', task }
beforeEach(() => { localStorage.clear(); vi.stubGlobal('crypto', webcrypto); vi.restoreAllMocks() })

test('reload restores exact legacy command identity without persisting task text or upgrading its response format', async () => {
  expect(await rememberPlanningSubmission(submission)).toBe('')
  const saved = readPlanningRecovery()
  expect(saved.warning).toBe('')
  expect(saved.items).toHaveLength(1)
  expect(localStorage.getItem(localStorage.key(0)!)).not.toContain(task.description)
  const restored = await restorePlanningSubmission(saved.items[0]!, { ...task, status: 'completed' })
  expect(restored).toEqual({ ...submission, task: { ...task, status: 'completed' } })
  expect(restored).not.toHaveProperty('responseFormat')
})

test('rejects changed task content and project instead of replaying an old ID with different input', async () => {
  await rememberPlanningSubmission(submission)
  const saved = readPlanningRecovery().items[0]!
  await expect(restorePlanningSubmission(saved, { ...task, description: 'Changed request' })).rejects.toThrow('task input changed')
  await expect(restorePlanningSubmission(saved, { ...task, projectId: 'other-project' })).rejects.toThrow('task input changed')
})

test('preserves captured structured-output version and independent submissions from other tabs', async () => {
  await rememberPlanningSubmission({ ...submission, responseFormat: 'planning_review_json_v1' })
  await rememberPlanningSubmission({ ...submission, id: 'another-tab-id' })
  const saved = readPlanningRecovery().items.find(item => item.id === submission.id)!
  expect((await restorePlanningSubmission(saved, task)).responseFormat).toBe('planning_review_json_v1')
  expect(forgetPlanningSubmission(submission.id)).toBe('')
  expect(readPlanningRecovery().items.map(item => item.id)).toEqual(['another-tab-id'])
})

test('storage denial remains visible while preserving the in-memory submission', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError') })
  expect(await rememberPlanningSubmission(submission)).toContain('retry storage is unavailable')
  expect(submission.id).toBe('original-command-id')
  vi.spyOn(Storage.prototype, 'length', 'get').mockImplementation(() => { throw new DOMException('Denied', 'SecurityError') })
  expect(readPlanningRecovery().warning).toContain('retry storage is unavailable')
})

test('malformed saved values never become executable submissions', async () => {
  await rememberPlanningSubmission(submission)
  const key = localStorage.key(0)!
  localStorage.setItem(key, JSON.stringify({ ...readPlanningRecovery().items[0], actorId: '../not-an-identity' }))
  expect(readPlanningRecovery().items).toEqual([])
  expect(readPlanningRecovery().warning).toContain('unreadable')
})
