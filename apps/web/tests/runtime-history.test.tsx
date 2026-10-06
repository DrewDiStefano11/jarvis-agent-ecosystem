import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { RuntimeRunHistory } from '../src/components/RuntimeRunHistory'
const store = vi.hoisted(() => ({ selectTask: vi.fn(), runtime: { actorId: 'operator', taskId: '', identities: [{ id: 'operator', display_name: 'Local planner' }], runs: [] as Array<Record<string, unknown>>, error: null as string | null, loading: false, loadingMore: false, nextOffset: 50 as number | null, pagesLoaded: 1, refreshRuntime: vi.fn(), loadMoreRuns: vi.fn() }, tasks: [{ id: 'task', title: 'Compare evidence' }] }))
vi.mock('../src/state/AppStore', () => ({ useAppStore: () => store }))
const record = (id: string, state: string, task = 'task') => ({ specification: { run_id: id, task_id: task, agent_id: 'operator', requested_operation: 'Review sources <script>literal</script>' }, state, version: 3, event_sequence_number: 3, attempt_count: 2, created_at: '2026-10-06T12:00:00Z', completed_at: null, recovery_status: 'none', status_detail: 'Recorded status' })
beforeEach(() => { store.runtime.runs = [record('run-a', 'paused'), record('run-b', 'succeeded', 'unloaded-task')]; store.runtime.error = null; store.runtime.loading = false; store.runtime.nextOffset = 50; store.runtime.pagesLoaded = 1; vi.clearAllMocks() })
it('filters only loaded native records and keeps unavailable task inspection disabled', async () => {
  const user = userEvent.setup(); render(<RuntimeRunHistory/>);
  const history = within(screen.getByRole('region', { name: 'Runtime history' }))
  expect(history.getByText(/not a complete history total/)).toBeInTheDocument()
  expect(history.getAllByRole('button', { name: 'Open task' })[1]).toBeDisabled()
  await user.click(history.getByRole('button', { name: 'Needs attention' }))
  expect(history.getByText('1 matching of 2 loaded runs')).toBeInTheDocument()
  await user.click(history.getByRole('button', { name: 'Open task' })); expect(store.selectTask).toHaveBeenCalledWith('task')
  await user.click(history.getByRole('button', { name: 'Finished' }))
  expect(history.getByText('run-b', { selector: 'code' })).toBeInTheDocument()
  await user.type(history.getByLabelText('Search runs'), 'missing')
  expect(history.getByText('No loaded runs match these filters.')).toBeInTheDocument()
})
it('shows actual bounded snapshot evidence as inert text without synthesizing events', async () => {
  store.runtime.runs = [{ ...record('run-a', 'failed'), failure: { category: 'provider', detail: 'x'.repeat(5000), timestamp: '2026-10-06T12:00:00Z' } }]
  const { container } = render(<RuntimeRunHistory/>);
  await userEvent.click(screen.getByText('Technical details'))
  expect(screen.getByText('Event sequence')).toBeInTheDocument()
  expect(screen.getByText('provider', { exact: true })).toBeInTheDocument()
  expect(screen.getByText(/\[truncated\]/)).toHaveTextContent('… [truncated]')
  expect(container.querySelector('script')).toBeNull()
  expect(screen.queryByText('Event sequence · newest first')).not.toBeInTheDocument()
})
it('distinguishes empty authorized pages from exhausted history and enforces the page cap', async () => {
  store.runtime.runs = []; const result = render(<RuntimeRunHistory/>);
  expect(screen.getByText('No authorized runs in the loaded pages. More pages are available.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Load next page' })); expect(store.runtime.loadMoreRuns).toHaveBeenCalledOnce()
  store.runtime.pagesLoaded = 4; result.rerender(<RuntimeRunHistory/>);
  expect(screen.queryByRole('button', { name: 'Load next page' })).not.toBeInTheDocument()
  expect(screen.getByText(/History is bounded to four pages/)).toBeInTheDocument()
  store.runtime.nextOffset = null; result.rerender(<RuntimeRunHistory/>);
  expect(screen.getByText('No authorized runs in this selection.')).toBeInTheDocument()
})
it('displays refresh errors and disables reads while a shared request is pending', () => {
  store.runtime.error = 'Forbidden'; store.runtime.loading = true; render(<RuntimeRunHistory/>);
  expect(screen.getByRole('alert')).toHaveTextContent('Forbidden')
  expect(screen.getByRole('button', { name: 'Refresh runtime' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Load next page' })).toBeDisabled()
})
