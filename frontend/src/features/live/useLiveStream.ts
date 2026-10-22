import { type RefObject, useCallback, useEffect, useState } from 'react'
import type Hls from 'hls.js'
import { ApiError, http } from '@/api/client'
import type { StreamKind, StreamSession } from '@/api/types'
import { keepaliveStream, startStream, stopStream, stopStreamOnUnload } from './api'

export type LiveState =
  | { phase: 'idle' }
  | { phase: 'connecting' }
  | { phase: 'live' }
  | { phase: 'reconnecting'; attempt: number }
  /** Terminal until the user retries: the server said no for a reason retrying would not fix. */
  | { phase: 'limited' | 'unavailable' | 'failed'; message: string }

export const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000]
export const MAX_ATTEMPTS = BACKOFF_MS.length + 1
const STALL_MS = 20_000

// Starting a stream makes the server decode and re-encode video, which is CPU-heavy for the first seconds. Opening a whole
// wall at once saturated the server, made segments arrive late, tripped the stall detector and restarted everything in
// a loop. Starts are therefore admitted a few at a time; a slot is freed as soon as the stream plays (or fails).
export const MAX_CONCURRENT_STARTS = 3
const SLOT_TIMEOUT_MS = 25_000
let activeStarts = 0
const waiting: (() => void)[] = []

/** Tests only: forget slots held by hooks from a previous test. */
export function resetStartGateForTests(): void {
  activeStarts = 0
  waiting.length = 0
}

function acquireStartSlot(): Promise<() => void> {
  return new Promise((resolve) => {
    const grant = () => {
      activeStarts += 1
      let released = false
      const timer = setTimeout(() => release(), SLOT_TIMEOUT_MS) // never let a stuck start block the queue
      const release = () => {
        if (released) return
        released = true
        clearTimeout(timer)
        activeStarts -= 1
        waiting.shift()?.()
      }
      resolve(release)
    }
    if (activeStarts < MAX_CONCURRENT_STARTS) grant()
    else waiting.push(grant)
  })
}

interface Options {
  cameraId: string
  stream: StreamKind
  /** False while the tile is off screen or the tab is hidden: no session exists then. */
  enabled: boolean
  videoRef: RefObject<HTMLVideoElement | null>
}

const NO_RETRY: Record<string, 'limited' | 'unavailable'> = {
  nvr_stream_limit: 'limited',
  stream_limit: 'limited',
  stream_not_available: 'unavailable',
}

export function useLiveStream({ cameraId, stream, enabled, videoRef }: Options) {
  const [state, setState] = useState<LiveState>({ phase: 'idle' })
  const [transcoded, setTranscoded] = useState<boolean | null>(null)
  const [nonce, setNonce] = useState(0)
  const retry = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (!enabled) {
      setState({ phase: 'idle' })
      return
    }
    let cancelled = false
    let session: StreamSession | null = null
    let hls: Hls | null = null
    let keepTimer: ReturnType<typeof setInterval> | undefined
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let stallTimer: ReturnType<typeof setTimeout> | undefined
    let failures = 0
    let releaseSlot: (() => void) | null = null
    const video = videoRef.current
    const freeSlot = () => {
      releaseSlot?.()
      releaseSlot = null
    }

    const teardown = () => {
      clearInterval(keepTimer)
      clearTimeout(retryTimer)
      clearTimeout(stallTimer)
      keepTimer = retryTimer = stallTimer = undefined
      freeSlot()
      hls?.destroy()
      hls = null
      if (video) {
        video.removeAttribute('src')
        video.load()
      }
      if (session) {
        void stopStream(session.id).catch(() => undefined) // expiry on the server covers a failed stop
        session = null
      }
    }

    const onUnload = () => session && stopStreamOnUnload(session.id)

    const scheduleRetry = (message: string) => {
      teardown()
      if (cancelled) return
      failures += 1
      if (failures >= MAX_ATTEMPTS) {
        setState({ phase: 'failed', message })
        return
      }
      setState({ phase: 'reconnecting', attempt: failures })
      retryTimer = setTimeout(() => void begin(), BACKOFF_MS[failures - 1] ?? 15000)
    }

    // A single play() right after the manifest can be rejected before any media exists, so also try once data is ready.
    const onCanPlay = () => void video?.play().catch(() => undefined)
    const onPlaying = () => {
      failures = 0
      freeSlot()
      clearTimeout(stallTimer)
      setState({ phase: 'live' })
    }
    const onWaiting = () => {
      clearTimeout(stallTimer)
      // Be patient first: a loaded server delays segments without the stream being broken. Nudge the player once, and
      // only rebuild the session if it is still stuck afterwards.
      stallTimer = setTimeout(() => {
        hls?.startLoad()
        stallTimer = setTimeout(() => scheduleRetry('The stream stalled'), STALL_MS)
      }, STALL_MS)
    }

    const attach = async (s: StreamSession) => {
      if (!video) return
      video.muted = true
      const { default: HlsLib } = await import('hls.js') // loaded on first use so the main bundle stays small
      if (cancelled) return
      if (!HlsLib.isSupported()) {
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = s.hls_url // Safari plays HLS natively
          void video.play().catch(() => undefined)
        } else {
          setState({ phase: 'unavailable', message: 'This browser cannot play HLS video.' })
        }
        return
      }
      hls = new HlsLib({
        enableWorker: true,
        lowLatencyMode: true, // the gateway serves low-latency HLS (0.5 s parts)
        maxBufferLength: 4,
        backBufferLength: 5,
        liveSyncDuration: 2.5, // stay ~2.5 s behind the edge: enough cushion to play smoothly, far less than 3 whole segments
        liveMaxLatencyDuration: 8,
        maxLiveSyncPlaybackRate: 1.5, // drift behind the live edge is caught up by playing slightly fast
      })
      hls.on(HlsLib.Events.MANIFEST_PARSED, () => void video.play().catch(() => undefined))
      hls.on(HlsLib.Events.ERROR, (_e, data) => {
        if (!data.fatal) return
        const code = data.response?.code
        if (code === 401) {
          // The login token expired mid-stream. Video requests bypass the app's refresh logic, so refresh it through an
          // ordinary API call and resume loading without tearing the session down.
          http
            .get('/auth/me')
            .then(() => hls?.startLoad())
            .catch(() => scheduleRetry('Your session expired'))
        } else if (data.type === HlsLib.ErrorTypes.MEDIA_ERROR) hls?.recoverMediaError()
        else if (code === 404 || code === 410 || code === 403) scheduleRetry('The stream is not available yet')
        else if (data.type === HlsLib.ErrorTypes.NETWORK_ERROR) scheduleRetry('Lost connection to the stream')
        else scheduleRetry('Playback failed')
      })
      hls.loadSource(s.hls_url)
      hls.attachMedia(video)
    }

    const begin = async () => {
      if (cancelled) return
      setState(failures === 0 ? { phase: 'connecting' } : { phase: 'reconnecting', attempt: failures })
      const release = await acquireStartSlot()
      if (cancelled) {
        release()
        return
      }
      releaseSlot = release
      let s: StreamSession
      try {
        s = await startStream(cameraId, stream)
      } catch (err) {
        if (cancelled) return
        freeSlot()
        if (err instanceof ApiError && NO_RETRY[err.code]) setState({ phase: NO_RETRY[err.code]!, message: err.message })
        else scheduleRetry(err instanceof Error ? err.message : 'Could not start the stream')
        return
      }
      if (cancelled) {
        void stopStream(s.id).catch(() => undefined)
        return
      }
      session = s
      setTranscoded(s.transcoded)
      await attach(s)
      keepTimer = setInterval(() => {
        void keepaliveStream(s.id).catch((err: unknown) => {
          const gone = err instanceof ApiError && (err.status === 404 || err.status === 410)
          if (gone) scheduleRetry('The stream session ended')
        })
      }, s.keepalive_every_seconds * 1000)
    }

    window.addEventListener('pagehide', onUnload)
    video?.addEventListener('playing', onPlaying)
    video?.addEventListener('waiting', onWaiting)
    video?.addEventListener('canplay', onCanPlay)
    void begin()

    return () => {
      cancelled = true
      window.removeEventListener('pagehide', onUnload)
      video?.removeEventListener('playing', onPlaying)
      video?.removeEventListener('waiting', onWaiting)
      video?.removeEventListener('canplay', onCanPlay)
      teardown()
    }
  }, [cameraId, stream, enabled, nonce, videoRef])

  return { state, transcoded, retry }
}
