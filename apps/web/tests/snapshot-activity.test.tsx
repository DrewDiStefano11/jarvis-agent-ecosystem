import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { SnapshotActivity } from '../src/components/SnapshotActivity'
import type { AuditEvent } from '../src/types/contracts'

test('event counts deduplicate IDs and omit invalid timestamps without manufacturing gaps', async () => {
  const onDate = vi.fn()
  const events = [
    { id: 'a', timestamp: '2026-01-01T23:00:00-05:00' },
    { id: 'a', timestamp: '2026-01-01T23:00:00-05:00' },
    { id: 'b', timestamp: '2026-01-02T11:00:00Z' },
    { id: 'c', timestamp: '2026-01-05T11:00:00Z' },
    { id: 'invalid', timestamp: 'invalid' },
  ] as AuditEvent[]
  render(<SnapshotActivity events={events} onDate={onDate}/>)
  await userEvent.click(screen.getByRole('button', { name: 'Inspect 2 recorded events on 2026-01-02' }))
  expect(onDate).toHaveBeenCalledWith('2026-01-02')
  await userEvent.click(screen.getByText('Read chart values'))
  expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(3)
  expect(screen.queryByText('2026-01-03')).not.toBeInTheDocument()
})
test('recent range excludes old and future events and explains unavailable history', async () => {
  render(<SnapshotActivity events={[{ id: 'old', timestamp: '2000-01-01' }, { id: 'future', timestamp: '2099-01-01' }] as AuditEvent[]}/>)
  await userEvent.selectOptions(screen.getByLabelText('Period'), '7')
  expect(screen.getByText('No recorded events in this period')).toBeInTheDocument()
  expect(screen.getByText(/measurements are not available/)).toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})
