import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from '@/api/client'
import type { Camera } from '@/api/types'
import { LiveCameraPlayer } from './LiveCameraPlayer'
import type { LiveState } from './useLiveStream'

const stream = vi.hoisted(() => ({ state: { phase: 'connecting' } as LiveState, transcoded: null as boolean | null, retry: vi.fn(), calls: [] as unknown[] }))
vi.mock('./useLiveStream', () => ({
  useLiveStream: (opts: unknown) => {
    stream.calls.push(opts)
    return { state: stream.state, transcoded: stream.transcoded, retry: stream.retry }
  },
}))

const camera = (over: Partial<Camera> = {}): Camera => ({
  id: 'c1', nvr_id: 'n1', nvr_name: 'HO NVR', campus_id: 'k1', campus_name: 'Head Office', channel_number: 3, name: 'Gate',
  model: null, status: 'online', status_detail: null, status_checked_at: null, last_discovered_at: null,
  main_stream: null, sub_stream: null, is_favorite: false, ...over,
})

const lastOpts = () => stream.calls.at(-1) as { enabled: boolean; stream: string; cameraId: string }

function renderTile(c: Camera, props: Partial<Parameters<typeof LiveCameraPlayer>[0]> = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <LiveCameraPlayer camera={c} {...props} />
    </QueryClientProvider>,
  )
}

describe('LiveCameraPlayer', () => {
  beforeEach(() => {
    stream.state = { phase: 'connecting' }
    stream.transcoded = null
    stream.retry.mockReset()
    stream.calls.length = 0
  })
  afterEach(() => vi.restoreAllMocks())

  it('names the camera and its NVR and channel', () => {
    renderTile(camera())
    expect(screen.getByText('Gate')).toBeInTheDocument()
    expect(screen.getByText('HO NVR · ch 3')).toBeInTheDocument()
  })

  it('says what the stream is really doing, never showing a fake picture', () => {
    const { rerender } = renderTile(camera())
    expect(screen.getByText('Connecting…')).toBeInTheDocument()
    stream.state = { phase: 'reconnecting', attempt: 2 }
    rerender(<QueryClientProvider client={new QueryClient()}><LiveCameraPlayer camera={camera()} /></QueryClientProvider>)
    expect(screen.getByText('Reconnecting (attempt 2)…')).toBeInTheDocument()
  })

  it('shows LIVE only when playing, and says whether the video was converted', () => {
    stream.state = { phase: 'live' }
    stream.transcoded = true
    renderTile(camera())
    expect(screen.getByText(/LIVE/)).toBeInTheDocument()
    expect(screen.getByTitle('Converted to H.264 on the server for this browser')).toBeInTheDocument()
    expect(screen.queryByText('Connecting…')).not.toBeInTheDocument()
  })

  it('marks the main stream as HD', () => {
    stream.state = { phase: 'live' }
    renderTile(camera(), { stream: 'main' })
    expect(screen.getByText(/LIVE HD/)).toBeInTheDocument()
    expect(lastOpts().stream).toBe('main')
  })

  it.each(['limited', 'unavailable', 'failed'] as const)('shows the %s reason and a working Try again button', async (phase) => {
    stream.state = { phase, message: 'This NVR already has 8 live streams' }
    renderTile(camera())
    expect(screen.getByText('This NVR already has 8 live streams')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(stream.retry).toHaveBeenCalledTimes(1)
  })

  it('does not stream a camera the NVR reported as not responding, until the user insists', async () => {
    stream.state = { phase: 'idle' }
    renderTile(camera({ status: 'offline' }))
    expect(lastOpts().enabled).toBe(false)
    expect(screen.getByText('Camera not responding')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try anyway' }))
    expect(lastOpts().enabled).toBe(true)
  })

  it('never streams a camera removed from the NVR and offers no way to force it', () => {
    stream.state = { phase: 'idle' }
    renderTile(camera({ status: 'unavailable' }))
    expect(lastOpts().enabled).toBe(false)
    expect(screen.getByText('No longer reported by the NVR')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try anyway' })).not.toBeInTheDocument()
  })

  it('holds no stream while paused (another camera is fullscreen)', () => {
    stream.state = { phase: 'idle' }
    renderTile(camera(), { paused: true })
    expect(lastOpts().enabled).toBe(false)
    expect(screen.getByText('Paused while another camera is fullscreen')).toBeInTheDocument()
  })

  it('streams an online camera when on screen and not paused', () => {
    renderTile(camera())
    expect(lastOpts()).toMatchObject({ enabled: true, stream: 'sub', cameraId: 'c1' })
  })

  it('stars and un-stars a camera through the API', async () => {
    const put = vi.spyOn(http, 'put').mockResolvedValue({ data: {} })
    const del = vi.spyOn(http, 'delete').mockResolvedValue({ data: {} })
    const { unmount } = renderTile(camera())
    await userEvent.click(screen.getByRole('button', { name: 'Add Gate to favorites' }))
    expect(put).toHaveBeenCalledWith('/cameras/c1/favorite')
    unmount()
    renderTile(camera({ is_favorite: true }))
    const star = screen.getByRole('button', { name: 'Remove Gate from favorites' })
    expect(star).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(star)
    expect(del).toHaveBeenCalledWith('/cameras/c1/favorite')
  })

  it('offers fullscreen only when a handler is given, and calls it', async () => {
    const onFullscreen = vi.fn()
    const { unmount } = renderTile(camera())
    expect(screen.queryByRole('button', { name: /fullscreen/ })).not.toBeInTheDocument()
    unmount()
    renderTile(camera(), { onFullscreen })
    await userEvent.click(screen.getByRole('button', { name: 'Open Gate fullscreen' }))
    expect(onFullscreen).toHaveBeenCalledTimes(1)
  })

  it('the large (fullscreen) tile has no favorite control', () => {
    renderTile(camera(), { large: true })
    expect(screen.queryByRole('button', { name: /favorites/ })).not.toBeInTheDocument()
  })

  it('keeps a consistent 16:9 shape', () => {
    renderTile(camera())
    expect(screen.getByTestId('tile-c1').className).toContain('aspect-video')
  })
})
