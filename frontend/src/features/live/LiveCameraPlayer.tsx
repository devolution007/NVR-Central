import { Loader2, Maximize2, Star, TriangleAlert, VideoOff } from 'lucide-react'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { Camera, StreamKind } from '@/api/types'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useToggleFavorite } from './api'
import { useLiveStream } from './useLiveStream'
import { useWatchable } from './useWatchable'

interface Props {
  camera: Camera
  stream?: StreamKind
  onFullscreen?: () => void
  /** Hold no stream (used by the wall while a camera is open fullscreen on top of it). */
  paused?: boolean
  /** Larger text and no favorite control, for the fullscreen dialog. */
  large?: boolean
  className?: string
}

/** One camera tile. Always 16:9, and always honest: the overlay says what the stream is really doing. */
export function LiveCameraPlayer({ camera, stream = 'sub', onFullscreen, paused = false, large = false, className }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const watchable = useWatchable(box)
  const [tryAnyway, setTryAnyway] = useState(false)
  const favorite = useToggleFavorite()

  // A camera the NVR already reported as not responding is not streamed automatically: every attempt is a wasted
  // connection to the NVR. The user can still try it.
  const blocked = camera.status === 'unavailable' || (camera.status === 'offline' && !tryAnyway)
  const { state, transcoded, retry } = useLiveStream({ cameraId: camera.id, stream, enabled: watchable && !blocked && !paused, videoRef: video })

  const live = state.phase === 'live'
  const busy = state.phase === 'connecting' || state.phase === 'reconnecting'
  const problem = state.phase === 'limited' || state.phase === 'unavailable' || state.phase === 'failed'

  return (
    <div ref={box} className={cn('group relative aspect-video overflow-hidden rounded-lg border bg-black', className)} data-testid={`tile-${camera.id}`}>
      <video ref={video} className={cn('size-full object-contain', !live && 'invisible')} muted autoPlay playsInline aria-label={`Live video: ${camera.name}`} />

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 bg-gradient-to-b from-black/70 to-transparent p-2 text-white">
        <div className="min-w-0">
          <p className={cn('truncate font-medium', large ? 'text-base' : 'text-xs')}>{camera.name}</p>
          <p className={cn('truncate opacity-80', large ? 'text-sm' : 'text-[10px]')}>
            {camera.nvr_name} · ch {camera.channel_number}
          </p>
        </div>
        <div className="pointer-events-auto flex items-center gap-1">
          {live && (
            <span className="flex items-center gap-1 rounded bg-black/50 px-1.5 py-0.5 text-[10px] font-semibold" title={transcoded ? 'Converted to H.264 on the server for this browser' : 'Played as sent by the camera, no conversion'}>
              <span className="size-1.5 rounded-full bg-red-500" aria-hidden /> LIVE{stream === 'main' ? ' HD' : ''}
            </span>
          )}
          {!large && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-white hover:bg-white/20 hover:text-white"
              aria-label={camera.is_favorite ? `Remove ${camera.name} from favorites` : `Add ${camera.name} to favorites`}
              aria-pressed={camera.is_favorite}
              onClick={() => favorite.mutate({ cameraId: camera.id, favorite: !camera.is_favorite }, { onError: (e) => toast.error(errorMessage(e)) })}
            >
              <Star className={cn('size-4', camera.is_favorite && 'fill-yellow-400 text-yellow-400')} />
            </Button>
          )}
          {onFullscreen && (
            <Button variant="ghost" size="icon" className="size-7 text-white hover:bg-white/20 hover:text-white" aria-label={`Open ${camera.name} fullscreen`} onClick={onFullscreen}>
              <Maximize2 className="size-4" />
            </Button>
          )}
        </div>
      </div>

      {!live && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-3 text-center text-xs text-white/90" role="status" aria-live="polite">
          {busy && (
            <>
              <Loader2 className="size-6 animate-spin" aria-hidden />
              <span>{state.phase === 'reconnecting' ? `Reconnecting (attempt ${state.attempt})…` : 'Connecting…'}</span>
            </>
          )}
          {state.phase === 'idle' && !blocked && (
            <>
              <VideoOff className="size-6 opacity-60" aria-hidden />
              <span className="opacity-70">{paused ? 'Paused while another camera is fullscreen' : 'Paused while off screen'}</span>
            </>
          )}
          {blocked && (
            <>
              <VideoOff className="size-6 opacity-60" aria-hidden />
              <span>{camera.status === 'unavailable' ? 'No longer reported by the NVR' : 'Camera not responding'}</span>
              {camera.status === 'offline' && (
                <Button size="sm" variant="secondary" onClick={() => setTryAnyway(true)}>
                  Try anyway
                </Button>
              )}
            </>
          )}
          {problem && (
            <>
              <TriangleAlert className="size-6 text-yellow-400" aria-hidden />
              <span className="max-w-xs">{state.message}</span>
              <Button size="sm" variant="secondary" onClick={retry}>
                Try again
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
