import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from '@/api/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { Nvr } from '@/api/types'
import { NvrActions } from './NvrActions'

const nvr = (over: Partial<Nvr> = {}): Nvr => ({
  id: 'n1', campus_id: 'c1', campus_name: 'Head Office', name: 'Main NVR', connection_type: 'lan', host: '10.0.0.5',
  http_port: 80, rtsp_port: 554, username: 'admin', has_password: true, p2p_serial_masked: null, status: 'connected',
  status_message: null, model: null, firmware: null, enabled: true, camera_count: 0, utc_offset_minutes: null,
  last_checked_at: null, last_success_at: null, created_at: '', updated_at: '', ...over,
})

function renderActions(n: Nvr) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <TooltipProvider>
        <NvrActions nvr={n} labelled />
      </TooltipProvider>
    </QueryClientProvider>,
  )
}

describe('NvrActions', () => {
  afterEach(() => vi.restoreAllMocks())

  it('runs the check straight away for a healthy NVR', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({ data: {} })
    renderActions(nvr())
    await userEvent.click(screen.getByRole('button', { name: /Test connection/ }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/nvrs/n1/test', null, { params: undefined }))
  })

  it('asks before retrying credentials the NVR already rejected, and only then forces it', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({ data: {} })
    renderActions(nvr({ status: 'auth_failed' }))
    await userEvent.click(screen.getByRole('button', { name: /Test connection/ }))
    expect(post).not.toHaveBeenCalled() // nothing sent to the NVR yet
    expect(await screen.findByText(/counts toward the NVR's own lockout/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try anyway' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/nvrs/n1/test', null, { params: { force: true } }))
  })

  it('cancelling the lockout warning sends nothing', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({ data: {} })
    renderActions(nvr({ status: 'auth_failed' }))
    await userEvent.click(screen.getByRole('button', { name: /Reconnect/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(post).not.toHaveBeenCalled()
  })

  it('disables every action while a check is running or the NVR is disabled', () => {
    const { unmount } = renderActions(nvr({ status: 'connecting' }))
    screen.getAllByRole('button').forEach((b) => expect(b).toBeDisabled())
    unmount()
    renderActions(nvr({ enabled: false }))
    screen.getAllByRole('button').forEach((b) => expect(b).toBeDisabled())
  })
})
