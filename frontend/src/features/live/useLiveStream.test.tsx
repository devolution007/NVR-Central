import { act, renderHook } from '@testing-library/react'
import { ApiError } from '@/api/client'
import type { StreamSession } from '@/api/types'
import * as api from './api'
import { BACKOFF_MS, MAX_ATTEMPTS, MAX_CONCURRENT_STARTS, resetStartGateForTests, useLiveStream } from './useLiveStream'

// ---- a controllable stand-in for hls.js (hoisted: vi.mock factories run before the rest of the module) --------------
type Handler = (event: string, data: Record<string, unknown>) => void
interface FakeHlsInstance {
  handlers: Record<string, Handler>
  loaded: string | null
  destroyed: boolean
  startLoad: ReturnType<typeof vi.fn>
  recoverMediaError: ReturnType<typeof vi.fn>
  fail(data: Record<string, unknown>): void
}
const { FakeHls, instances, httpGet } = vi.hoisted(() => {
  const instances: FakeHlsInstance[] = []
  class FakeHls implements FakeHlsInstance {
    static isSupported = () => true
    static Events = { MANIFEST_PARSED: 'manifest', ERROR: 'error' }
    static ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' }
    handlers: Record<string, Handler> = {}
    loaded: string | null = null
    destroyed = false
    startLoad = vi.fn()
    recoverMediaError = vi.fn()
    constructor() {
      instances.push(this)
    }
    on(event: string, handler: Handler) {
      this.handlers[event] = handler
    }
    loadSource(url: string) {
      this.loaded = url
    }
    attachMedia() {}
    destroy() {
      this.destroyed = true
    }
    fail(data: Record<string, unknown>) {
      this.handlers[FakeHls.Events.ERROR]?.('error', { fatal: true, ...data })
    }
  }
  return { FakeHls, instances, httpGet: vi.fn() }
})
vi.mock('hls.js', () => ({ default: FakeHls }))
vi.mock('./api', () => ({
  startStream: vi.fn(),
  keepaliveStream: vi.fn(),
  stopStream: vi.fn(),
  stopStreamOnUnload: vi.fn(),
}))
vi.mock('@/api/client', async (orig) => ({ ...(await orig<typeof import('@/api/client')>()), http: { get: httpGet } }))

const startStream = vi.mocked(api.startStream)
const keepaliveStream = vi.mocked(api.keepaliveStream)
const stopStream = vi.mocked(api.stopStream)

let n = 0
const session = (over: Partial<StreamSession> = {}): StreamSession => ({
  id: `s${++n}`, camera_id: 'cam', stream: 'sub', hls_url: `/api/v1/stream-sessions/s${n}/hls/index.m3u8`,
  transcoded: false, expires_at: '', keepalive_every_seconds: 20, ...over,
})

function mount(opts: { enabled?: boolean; cameraId?: string } = {}) {
  const video = document.createElement('video')
  const videoRef = { current: video } // one stable ref, like useRef in the real component (a new object per render re-runs the effect forever)
  const hook = renderHook((p: { enabled: boolean; cameraId: string }) => useLiveStream({ cameraId: p.cameraId, stream: 'sub', enabled: p.enabled, videoRef }), {
    initialProps: { enabled: opts.enabled ?? true, cameraId: opts.cameraId ?? 'cam' },
  })
  return { video, ...hook }
}
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const play = (video: HTMLVideoElement) => act(() => { video.dispatchEvent(new Event('playing')) })

describe('useLiveStream', () => {
  beforeEach(() => {
    // Only the timer functions: faking everything (setImmediate, microtasks) starves React's own scheduler.
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
    instances.length = 0
    n = 0 // session ids restart at s1 in every test
    resetStartGateForTests()
    startStream.mockReset().mockImplementation(async () => session())
    keepaliveStream.mockReset().mockResolvedValue()
    stopStream.mockReset().mockResolvedValue()
    httpGet.mockReset().mockResolvedValue({})
  })
  afterEach(() => vi.useRealTimers())

  it('starts a session, loads the stream, and is live once video actually plays', async () => {
    const { result, video } = mount()
    expect(result.current.state.phase).toBe('connecting')
    await flush()
    expect(startStream).toHaveBeenCalledWith('cam', 'sub')
    expect(instances).toHaveLength(1)
    expect(instances[0]!.loaded).toBe('/api/v1/stream-sessions/s1/hls/index.m3u8')
    expect(result.current.state.phase).toBe('connecting') // a session alone is not "live"
    play(video)
    expect(result.current.state.phase).toBe('live')
  })

  it('holds no session while disabled (off screen), starts when enabled, and releases when disabled again', async () => {
    const { result, rerender } = mount({ enabled: false })
    await flush()
    expect(startStream).not.toHaveBeenCalled()
    expect(result.current.state.phase).toBe('idle')
    rerender({ enabled: true, cameraId: 'cam' })
    await flush()
    expect(startStream).toHaveBeenCalledTimes(1)
    rerender({ enabled: false, cameraId: 'cam' })
    await flush()
    expect(stopStream).toHaveBeenCalledWith('s1')
    expect(instances[0]!.destroyed).toBe(true)
  })

  it('ends the session when the tile unmounts', async () => {
    const { unmount } = mount()
    await flush()
    unmount()
    expect(stopStream).toHaveBeenCalledWith('s1')
    expect(instances[0]!.destroyed).toBe(true)
  })

  it('a session that arrives after unmount is ended immediately and never played', async () => {
    let resolve!: (s: StreamSession) => void
    startStream.mockImplementationOnce(() => new Promise((r) => (resolve = r)))
    const { unmount } = mount()
    await flush()
    unmount()
    await act(async () => resolve(session({ id: 'late' })))
    expect(stopStream).toHaveBeenCalledWith('late')
    expect(instances).toHaveLength(0)
  })

  it.each([
    ['nvr_stream_limit', 'limited'],
    ['stream_limit', 'limited'],
    ['stream_not_available', 'unavailable'],
  ] as const)('%s is a final answer: shown to the user and not retried', async (code, phase) => {
    startStream.mockRejectedValue(new ApiError(code === 'stream_not_available' ? 409 : 429, code, 'because reasons'))
    const { result } = mount()
    await flush()
    expect(result.current.state).toEqual({ phase, message: 'because reasons' })
    await advance(60_000)
    expect(startStream).toHaveBeenCalledTimes(1)
  })

  it('"Try again" after a refusal starts over', async () => {
    startStream.mockRejectedValueOnce(new ApiError(429, 'nvr_stream_limit', 'full'))
    const { result } = mount()
    await flush()
    expect(result.current.state.phase).toBe('limited')
    act(() => result.current.retry())
    await flush()
    expect(startStream).toHaveBeenCalledTimes(2)
    expect(instances).toHaveLength(1)
  })

  it('retries a gateway outage with growing delays and recovers', async () => {
    startStream.mockRejectedValueOnce(new ApiError(503, 'streaming_unavailable', 'down')).mockRejectedValueOnce(new ApiError(503, 'streaming_unavailable', 'down'))
    const { result, video } = mount()
    await flush()
    expect(result.current.state).toEqual({ phase: 'reconnecting', attempt: 1 })
    await advance(BACKOFF_MS[0]! - 1)
    expect(startStream).toHaveBeenCalledTimes(1)
    await advance(1)
    expect(startStream).toHaveBeenCalledTimes(2)
    expect(result.current.state).toEqual({ phase: 'reconnecting', attempt: 2 })
    await advance(BACKOFF_MS[1]!)
    expect(startStream).toHaveBeenCalledTimes(3)
    play(video)
    expect(result.current.state.phase).toBe('live')
  })

  it('gives up with a message after the maximum number of attempts', async () => {
    startStream.mockRejectedValue(new ApiError(503, 'streaming_unavailable', 'gateway down'))
    const { result } = mount()
    await flush()
    for (const delay of BACKOFF_MS) await advance(delay)
    expect(startStream).toHaveBeenCalledTimes(MAX_ATTEMPTS)
    expect(result.current.state).toEqual({ phase: 'failed', message: 'gateway down' })
    await advance(120_000)
    expect(startStream).toHaveBeenCalledTimes(MAX_ATTEMPTS) // stops hammering the server
  })

  it('sends keepalives on the interval the server asked for', async () => {
    mount()
    await flush()
    await advance(19_999)
    expect(keepaliveStream).not.toHaveBeenCalled()
    await advance(1)
    expect(keepaliveStream).toHaveBeenCalledWith('s1')
    await advance(20_000)
    expect(keepaliveStream).toHaveBeenCalledTimes(2)
  })

  it('replaces a session the server says is gone (expired or revoked)', async () => {
    keepaliveStream.mockRejectedValueOnce(new ApiError(410, 'stream_session_expired', 'ended'))
    const { result } = mount()
    await flush()
    await advance(20_000)
    expect(stopStream).toHaveBeenCalledWith('s1')
    expect(result.current.state.phase).toBe('reconnecting')
    await advance(BACKOFF_MS[0]!)
    expect(startStream).toHaveBeenCalledTimes(2)
  })

  it('does not treat a one-off keepalive network error as the end of the session', async () => {
    keepaliveStream.mockRejectedValueOnce(new ApiError(0, 'network_error', 'offline'))
    mount()
    await flush()
    await advance(20_000)
    expect(stopStream).not.toHaveBeenCalled()
  })

  it('rebuilds the session when the stream files are gone (404)', async () => {
    mount()
    await flush()
    act(() => instances[0]!.fail({ type: 'networkError', response: { code: 404 } }))
    expect(instances[0]!.destroyed).toBe(true)
    await advance(BACKOFF_MS[0]!)
    expect(startStream).toHaveBeenCalledTimes(2)
  })

  it('refreshes an expired login and resumes loading instead of tearing the stream down (401)', async () => {
    mount()
    await flush()
    await act(async () => instances[0]!.fail({ type: 'networkError', response: { code: 401 } }))
    expect(httpGet).toHaveBeenCalledWith('/auth/me') // an ordinary API call refreshes the token
    expect(instances[0]!.startLoad).toHaveBeenCalled()
    expect(instances[0]!.destroyed).toBe(false)
    expect(startStream).toHaveBeenCalledTimes(1)
  })

  it('recovers media errors in place', async () => {
    mount()
    await flush()
    act(() => instances[0]!.fail({ type: 'mediaError' }))
    expect(instances[0]!.recoverMediaError).toHaveBeenCalled()
    expect(startStream).toHaveBeenCalledTimes(1)
  })

  it('ignores non-fatal player errors', async () => {
    mount()
    await flush()
    act(() => instances[0]!.handlers.error?.('error', { fatal: false, type: 'networkError' }))
    expect(instances[0]!.destroyed).toBe(false)
  })

  it('is patient with a stall: nudges the player first, rebuilds only if still stuck', async () => {
    const { video } = mount()
    await flush()
    play(video)
    act(() => video.dispatchEvent(new Event('waiting')))
    await advance(19_999)
    expect(instances[0]!.startLoad).not.toHaveBeenCalled()
    await advance(1)
    expect(instances[0]!.startLoad).toHaveBeenCalledTimes(1)
    expect(startStream).toHaveBeenCalledTimes(1)
    await advance(20_000)
    expect(instances[0]!.destroyed).toBe(true)
    await advance(BACKOFF_MS[0]!)
    expect(startStream).toHaveBeenCalledTimes(2)
  })

  it('playing again cancels a pending stall action', async () => {
    const { video } = mount()
    await flush()
    act(() => video.dispatchEvent(new Event('waiting')))
    play(video)
    await advance(60_000)
    expect(instances[0]!.startLoad).not.toHaveBeenCalled()
  })

  it('admits only a few stream starts at a time so a whole wall cannot swamp the server', async () => {
    const mounted = Array.from({ length: MAX_CONCURRENT_STARTS + 2 }, (_, i) => mount({ cameraId: `cam${i}` }))
    await flush()
    expect(startStream).toHaveBeenCalledTimes(MAX_CONCURRENT_STARTS)
    play(mounted[0]!.video) // the first stream plays, freeing a slot
    await flush()
    expect(startStream).toHaveBeenCalledTimes(MAX_CONCURRENT_STARTS + 1)
    play(mounted[1]!.video)
    await flush()
    expect(startStream).toHaveBeenCalledTimes(MAX_CONCURRENT_STARTS + 2)
  })

  it('a start that never plays cannot block the queue forever', async () => {
    Array.from({ length: MAX_CONCURRENT_STARTS + 1 }, (_, i) => mount({ cameraId: `cam${i}` }))
    await flush()
    expect(startStream).toHaveBeenCalledTimes(MAX_CONCURRENT_STARTS)
    await advance(25_000)
    expect(startStream.mock.calls.length).toBeGreaterThan(MAX_CONCURRENT_STARTS)
  })

  it('a refused start frees its slot for the next tile', async () => {
    startStream.mockRejectedValueOnce(new ApiError(429, 'nvr_stream_limit', 'full'))
    Array.from({ length: MAX_CONCURRENT_STARTS + 1 }, (_, i) => mount({ cameraId: `cam${i}` }))
    await flush()
    expect(startStream).toHaveBeenCalledTimes(MAX_CONCURRENT_STARTS + 1)
  })

  it('tells the server this browser can play H.265 only when it can', async () => {
    mount()
    await flush()
    expect(startStream).toHaveBeenCalledWith('cam', 'sub')
  })
})
