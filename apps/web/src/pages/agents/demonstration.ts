import type { Agent, Department, Task } from '../../types/contracts'

export type DemonstrationKind = '' | 'permanent' | 'temporary'

export function demonstrationQuery(agent: Agent, departments: readonly Department[], tasks: readonly Task[]): string {
  const department = departments.find(item => item.id === agent.departmentId)?.name ?? ''
  const task = tasks.find(item => item.id === agent.currentTaskId)?.title ?? ''
  return `${agent.name} ${agent.role} ${agent.description} ${department} ${task} ${agent.capabilities.join(' ')}`.toLowerCase()
}

export function filterDemonstrationAgents(
  agents: readonly Agent[],
  departments: readonly Department[],
  tasks: readonly Task[],
  options: { query: string; status: string; departmentId: string; kind: DemonstrationKind },
): Agent[] {
  const needle = options.query.trim().toLowerCase()
  return agents.filter(agent => {
    if (needle && !demonstrationQuery(agent, departments, tasks).includes(needle)) return false
    if (options.status && agent.status !== options.status) return false
    if (options.departmentId && agent.departmentId !== options.departmentId) return false
    if (options.kind === 'permanent' && agent.isTemporary) return false
    if (options.kind === 'temporary' && !agent.isTemporary) return false
    return true
  })
}

export function demonstrationStatuses(agents: readonly Agent[]): string[] {
  return [...new Set(agents.map(agent => agent.status))].sort()
}
