import type { Task, TaskStatus } from '../types/contracts'

export const workGroups = [
  { key: 'active', label: 'In progress', color: '#a985ff', statuses: ['planning', 'assigned', 'in_progress', 'under_review', 'retrying'] },
  { key: 'queued', label: 'Queued', color: '#6ba8ff', statuses: ['queued'] },
  { key: 'waiting', label: 'Waiting', color: '#f7ca80', statuses: ['waiting', 'waiting_for_approval', 'revision_requested', 'paused'] },
  { key: 'completed', label: 'Completed', color: '#64dfc2', statuses: ['completed'] },
  { key: 'failed', label: 'Failed', color: '#ff9baf', statuses: ['failed'] },
  { key: 'cancelled', label: 'Cancelled', color: '#8590ab', statuses: ['cancelled'] },
] as const
export type WorkFilter = typeof workGroups[number]['key'] | 'all' | 'open'
export function matchesWorkFilter(status: TaskStatus, filter: WorkFilter) {
  return filter === 'all' || (filter === 'open' ? !['completed', 'failed', 'cancelled'].includes(status) : Boolean(workGroups.find(group => group.key === filter)?.statuses.some(value => value === status)))
}

/** Snapshot composition, never an execution success rate or throughput metric. */
export function WorkDistribution({ tasks, filter, onFilter }: { tasks: Task[]; filter: WorkFilter; onFilter: (filter: WorkFilter) => void }) {
  const groups = workGroups.map(group => ({ ...group, count: tasks.filter(task => group.statuses.some(status => status === task.status)).length }))
  const circumference = 2 * Math.PI * 70
  const segments = groups.reduce<{ count: number; color: string; offset: number }[]>((result, group) => {
    const previous = result.at(-1)
    return [...result, { count: group.count, color: group.color, offset: previous ? previous.offset + previous.count : 0 }]
  }, [])
  return <figure className="work-distribution">
    <div className="distribution-ring">
      <svg viewBox="0 0 180 180" aria-hidden="true"><circle cx="90" cy="90" r="70" fill="none" stroke="#293247" strokeWidth="22"/>
        {tasks.length > 0 && segments.filter(segment => segment.count > 0).map(segment => <circle key={segment.color} cx="90" cy="90" r="70" fill="none" stroke={segment.color} strokeWidth="22" strokeDasharray={`${segment.count / tasks.length * circumference} ${circumference}`} strokeDashoffset={-segment.offset / tasks.length * circumference} transform="rotate(-90 90 90)"/>)}</svg>
      <div><strong>{tasks.length}</strong><span>task records</span></div>
    </div>
    <figcaption>
      <div className="distribution-legend" role="group" aria-label="Filter task records by status">{groups.map(group => <button key={group.key} aria-label={`${group.label}, ${group.count} task records`} aria-pressed={filter === group.key} onClick={() => onFilter(filter === group.key ? 'all' : group.key)} title={`${group.count} ${group.label.toLowerCase()} task records. Filter the request list.`}><span className="chart-key" style={{ background: group.color }} aria-hidden="true"/><span>{group.label}</span><strong>{group.count}</strong></button>)}</div>
      <p className="mc-note">Current snapshot, including child tasks and demonstrations. Select a status to filter requests below.</p>
    </figcaption>
  </figure>
}
