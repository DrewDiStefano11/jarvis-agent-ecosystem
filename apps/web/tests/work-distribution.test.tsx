import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { matchesWorkFilter, WorkDistribution, workGroups } from '../src/components/WorkDistribution'
import type { Task, TaskStatus } from '../src/types/contracts'

test('every contracted task status belongs to exactly one chart segment', () => {
  const statuses: TaskStatus[] = ['queued', 'planning', 'assigned', 'in_progress', 'waiting', 'waiting_for_approval', 'under_review', 'revision_requested', 'paused', 'failed', 'retrying', 'completed', 'cancelled']
  for (const status of statuses) expect(workGroups.filter(group => matchesWorkFilter(status, group.key))).toHaveLength(1)
})
test('distribution exposes real counts and keyboard-operable filters', async () => {
  const onFilter = vi.fn()
  render(<WorkDistribution tasks={[{ status: 'completed' }, { status: 'queued' }, { status: 'queued' }] as Task[]} filter="all" onFilter={onFilter}/>)
  const legend = within(screen.getByRole('group', { name: 'Filter task records by status' }))
  expect(legend.getByRole('button', { name: 'Queued, 2 task records' })).toHaveTextContent('2')
  await userEvent.click(legend.getByRole('button', { name: 'Queued, 2 task records' }))
  expect(onFilter).toHaveBeenCalledWith('queued')
  expect(screen.getByText(/including child tasks and demonstrations/)).toBeInTheDocument()
})
test('empty snapshot never invents a graph segment or measured success rate', () => {
  const { container } = render(<WorkDistribution tasks={[]} filter="all" onFilter={vi.fn()}/>)
  expect(container.querySelectorAll('circle')).toHaveLength(1)
  expect(screen.getByText('task records').previousElementSibling).toHaveTextContent('0')
  expect(screen.queryByText(/success rate/i)).not.toBeInTheDocument()
})
