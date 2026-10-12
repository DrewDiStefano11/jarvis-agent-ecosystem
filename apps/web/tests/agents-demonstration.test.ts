import { describe, expect, test } from 'vitest'
import type { Agent, Department, Task } from '../src/types/contracts'
import { filterDemonstrationAgents } from '../src/pages/agents/demonstration'

const department: Department = { id: 'research', name: 'Research', description: '', managerAgentId: null, agentIds: [] }
function agent(patch: Partial<Agent> & Pick<Agent, 'id' | 'name' | 'status'>): Agent {
  return {
    schemaVersion: '1.0', role: 'Specialist', description: 'Simulated role', goals: [], departmentId: 'research', managerId: null,
    currentTaskId: null, queuedTaskIds: [], progress: 0, statusMessage: '', capabilities: ['simulation'], allowedTools: [], deniedTools: [],
    approvalPolicy: {}, memoryAccess: {}, performance: { completionRate: 0, accuracyScore: 0, averageCompletionTime: 0, failedTaskCount: 0, userCorrectionCount: 0, reviewerScore: 0, reliabilityScore: 0.5 },
    resourceProfile: {}, office: { zone: '', deskId: '', spriteIdentifier: '', displayPosition: { x: 0, y: 0 }, currentAnimationState: 'idle', currentDestination: null, isInMeeting: false },
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', version: '1', deploymentStatus: 'simulated', isTemporary: false, ...patch,
  }
}

describe('demonstration agent discovery', () => {
  const tasks = [{ id: 'task-1', title: 'Fixture trip' } as Task]
  const agents = [
    agent({ id: 'jarvis', name: 'Jarvis', status: 'idle', role: 'Executive Manager', departmentId: 'executive' }),
    agent({ id: 'scout', name: 'Scout', status: 'researching', currentTaskId: 'task-1', isTemporary: true }),
  ]

  test('filters by name, status, department, and temporary records without mutating the list', () => {
    expect(filterDemonstrationAgents(agents, [department], tasks, { query: 'fixture', status: '', departmentId: '', kind: '' }).map(item => item.id)).toEqual(['scout'])
    expect(filterDemonstrationAgents(agents, [department], tasks, { query: '', status: 'idle', departmentId: '', kind: '' }).map(item => item.id)).toEqual(['jarvis'])
    expect(filterDemonstrationAgents(agents, [department], tasks, { query: '', status: '', departmentId: 'research', kind: 'temporary' }).map(item => item.id)).toEqual(['scout'])
    expect(filterDemonstrationAgents(agents, [department], tasks, { query: 'jarvis', status: '', departmentId: '', kind: 'temporary' })).toEqual([])
    expect(agents).toHaveLength(2)
  })
})
