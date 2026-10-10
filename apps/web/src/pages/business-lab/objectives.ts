import type { Task, TaskStatus } from '../../types/contracts'

export const OBJECTIVE_LANES = ['queued', 'planning', 'execution', 'completed', 'attention'] as const
export type ObjectiveLane = (typeof OBJECTIVE_LANES)[number]
export type ObjectiveSort = 'updated-desc' | 'updated-asc' | 'title'

export const LANE_COPY: Record<ObjectiveLane, { title: string; detail: string }> = {
  queued: { title: 'Queued', detail: 'Saved objectives. Planning has not started.' },
  planning: { title: 'Planning', detail: 'Assigned or being planned. This status does not authorize execution.' },
  execution: { title: 'Approved execution', detail: 'Recorded as in progress, waiting, or under review. Not evidence of unattended autonomy.' },
  completed: { title: 'Completed', detail: 'Recorded as finished.' },
  attention: { title: 'Needs attention', detail: 'Failed or cancelled. Inspect history before changing the request.' },
}

const STATUS_LANE: Record<TaskStatus, ObjectiveLane> = {
  queued: 'queued',
  planning: 'planning',
  assigned: 'planning',
  in_progress: 'execution',
  waiting: 'execution',
  waiting_for_approval: 'execution',
  under_review: 'execution',
  revision_requested: 'execution',
  paused: 'execution',
  retrying: 'execution',
  completed: 'completed',
  failed: 'attention',
  cancelled: 'attention',
}

export function objectiveLane(status: TaskStatus): ObjectiveLane {
  return STATUS_LANE[status]
}

export function isBusinessObjective(task: Task): boolean {
  return task.projectId === 'business-lab'
}

export function businessObjectives(tasks: readonly Task[]): Task[] {
  return tasks.filter(isBusinessObjective)
}

export function matchesObjectiveQuery(task: Task, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return `${task.title}\n${task.description}`.toLowerCase().includes(needle)
}

export function filterObjectives(
  tasks: readonly Task[],
  options: { query: string; status: string; lane: ObjectiveLane | '' },
): Task[] {
  return tasks.filter(task => isBusinessObjective(task)
    && matchesObjectiveQuery(task, options.query)
    && (!options.status || task.status === options.status)
    && (!options.lane || objectiveLane(task.status) === options.lane))
}

export function sortObjectives(tasks: readonly Task[], sort: ObjectiveSort): Task[] {
  return [...tasks].sort((left, right) => {
    if (sort === 'title') {
      const byTitle = left.title.localeCompare(right.title, undefined, { sensitivity: 'base' })
      return byTitle || left.id.localeCompare(right.id)
    }
    const leftTime = Date.parse(left.updatedAt)
    const rightTime = Date.parse(right.updatedAt)
    const delta = (Number.isNaN(leftTime) ? 0 : leftTime) - (Number.isNaN(rightTime) ? 0 : rightTime)
    if (delta !== 0) return sort === 'updated-asc' ? delta : -delta
    return left.id.localeCompare(right.id)
  })
}

export function laneCounts(tasks: readonly Task[]): Record<ObjectiveLane, number> {
  const counts: Record<ObjectiveLane, number> = { queued: 0, planning: 0, execution: 0, completed: 0, attention: 0 }
  for (const task of tasks) {
    if (isBusinessObjective(task)) counts[objectiveLane(task.status)] += 1
  }
  return counts
}

export function recordedStatuses(tasks: readonly Task[]): TaskStatus[] {
  return [...new Set(businessObjectives(tasks).map(task => task.status))].sort()
}

export function formatTimestamp(value: string): string {
  const parsed = Date.parse(value)
  if (Number.isNaN(parsed)) return value || 'Unknown time'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}

export function participantLabel(
  id: string,
  identities: readonly { id: string; display_name: string }[],
  agents: readonly { id: string; name: string }[],
): string {
  const identity = identities.find(item => item.id === id)
  if (identity) return `${identity.display_name} (runtime identity)`
  const agent = agents.find(item => item.id === id)
  if (agent) return `${agent.name} (demonstration agent)`
  return `${id} (unresolved id)`
}
