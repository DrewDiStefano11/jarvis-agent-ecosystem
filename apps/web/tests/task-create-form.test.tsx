import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { TaskCreateForm } from '../src/components/TaskCreateForm'

const { refresh } = vi.hoisted(() => ({ refresh: vi.fn() }))
vi.mock('../src/state/AppStore', () => ({ useAppStore: () => ({ refresh }) }))

test('finishing creation after navigation does not redirect the operator back to the old form', async () => {
  let finishRefresh!: () => void
  refresh.mockReturnValue(new Promise<void>(resolve => { finishRefresh = resolve }))
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: { id: 'created-task' } }) }))
  const onCreated = vi.fn()
  const form = render(<TaskCreateForm onCreated={onCreated}/>)
  await userEvent.type(screen.getByLabelText('Title'), 'Create before navigation')
  await userEvent.type(screen.getByLabelText('Description'), 'An operator can leave while status refresh is pending.')
  await userEvent.click(screen.getByRole('button', { name: 'Create task' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  form.unmount()
  await act(async () => { finishRefresh() })
  expect(onCreated).not.toHaveBeenCalled()
})
