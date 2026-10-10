import { describe, expect, test } from 'vitest'
import type { Task, TaskStatus } from '../src/types/contracts'
import {
  filterObjectives,
  laneCounts,
  objectiveLane,
  participantLabel,
  sortObjectives,
} from '../src/pages/business-lab/objectives'

const statuses: TaskStatus[] = ['queued', 'planning', 'assigned', 'in_progress', 'waiting', 'waiting_for_approval', 'under_review', 'revision_requested', 'paused', 'retrying', 'completed', 'failed', 'cancelled']

function task(patch: Partial<Task> & Pick<Task, 'id' | 'title' | 'status'>): Task {
  return {
    schemaVersion: '1.0', description: patch.description ?? `${patch.title} details`, request: patch.title,
    parentTaskId: null, childTaskIds: [], projectId: 'business-lab', createdBy: 'user', assignedManagerId: null,
    assignedAgentIds: [], priority: 'medium', progress: 0, statusMessage: '', dependencies: [], blockedBy: [],
    approvalIds: [], artifactIds: [], result: null, error: null, retryCount: 0, maxRetries: 0,
    createdAt: '2026-01-01T00:00:00Z', startedAt: null, updatedAt: '2026-01-01T00:00:00Z', completedAt: null,
    ...patch,
  }
}

describe('business lab objective organization', () => {
  test('maps every recorded status into a truthful workflow lane', () => {
    expect(statuses.map(objectiveLane)).toEqual([
      'queued', 'planning', 'planning', 'execution', 'execution', 'execution', 'execution', 'execution', 'execution', 'execution', 'completed', 'attention', 'attention',
    ])
  })

  test('searches title and description, filters status, and ignores other projects', () => {
    const rows = [
      task({ id: 'a', title: 'Market brief', description: 'Customer interviews', status: 'queued' }),
      task({ id: 'b', title: 'Pricing note', description: 'Internal only', status: 'completed', updatedAt: '2026-02-01T00:00:00Z' }),
      task({ id: 'c', title: 'Market brief', status: 'queued', projectId: 'other' }),
    ]
    expect(filterObjectives(rows, { query: 'interview', status: '', lane: '' }).map(item => item.id)).toEqual(['a'])
    expect(filterObjectives(rows, { query: '', status: 'completed', lane: '' }).map(item => item.id)).toEqual(['b'])
    expect(filterObjectives(rows, { query: 'market', status: 'queued', lane: 'completed' })).toEqual([])
    expect(laneCounts(rows)).toMatchObject({ queued: 1, completed: 1, planning: 0 })
  })

  test('sorts by title and update time without dropping ids', () => {
    const rows = [
      task({ id: 'b', title: 'Beta', status: 'queued', updatedAt: '2026-03-02T00:00:00Z' }),
      task({ id: 'a', title: 'alpha', status: 'queued', updatedAt: '2026-03-01T00:00:00Z' }),
    ]
    expect(sortObjectives(rows, 'title').map(item => item.id)).toEqual(['a', 'b'])
    expect(sortObjectives(rows, 'updated-asc').map(item => item.id)).toEqual(['a', 'b'])
    expect(sortObjectives(rows, 'updated-desc').map(item => item.id)).toEqual(['b', 'a'])
  })

  test('labels runtime identities separately from demonstration agents', () => {
    expect(participantLabel('real', [{ id: 'real', display_name: 'Analyst' }], [])).toBe('Analyst (runtime identity)')
    expect(participantLabel('demo', [], [{ id: 'demo', name: 'Scout' }])).toBe('Scout (demonstration agent)')
    expect(participantLabel('missing', [], [])).toBe('missing (unresolved id)')
  })
})
