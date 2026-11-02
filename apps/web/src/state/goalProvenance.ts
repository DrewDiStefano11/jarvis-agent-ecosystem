// Reserved built-in task IDs from apps/api/app/services/seed.py. Operator
// submissions receive generated IDs; titles and completion state grant no provenance.
const seededGoals = new Set(['task-completed', 'task-demo', 'task-queued', 'task-failed'])

export function isSeededGoal(taskId: string): boolean {
  return seededGoals.has(taskId)
}
