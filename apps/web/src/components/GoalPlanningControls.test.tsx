import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import { GoalPlanningControls } from './GoalPlanningControls'

const configure = vi.fn()
vi.mock('../state/AppStore', () => ({ useAppStore: () => ({ goals: { planning: { policy: { enabled: true, expiresAt: '2099-01-01T00:00:00Z', unavailableReason: 'worker_configuration_changed' }, busy: false, pending: { body: { maximumGoals: 2, expiresAt: '2099-01-01T00:00:00Z' } }, configure, refresh: vi.fn(), revoke: vi.fn(), error: '' } } }) }))
test('captured retry remains actionable when a new planning request is unavailable', async () => {
  render(<MemoryRouter><GoalPlanningControls/></MemoryRouter>)
  await userEvent.click(screen.getByText('Bounded planning', { exact: false }))
  const retry = screen.getByRole('button', { name: 'Retry captured consent' })
  expect(retry).toBeEnabled()
  await userEvent.click(retry)
  expect(configure).toHaveBeenCalledTimes(1)
})
