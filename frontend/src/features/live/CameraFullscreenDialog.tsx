import type { Camera } from '@/api/types'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { streamLabel } from '@/lib/format'
import { LiveCameraPlayer } from './LiveCameraPlayer'

/** Opens a camera on its main stream. Only mounted while open, so no HD session exists otherwise. */
export function CameraFullscreenDialog({ camera, onClose }: { camera: Camera | null; onClose: () => void }) {
  return (
    <Dialog open={camera !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[96vh] w-[96vw] max-w-[96vw] gap-3 p-3 sm:max-w-[min(96vw,calc(88vh*16/9))]">
        {camera && (
          <>
            <DialogHeader>
              <DialogTitle>{camera.name}</DialogTitle>
              <DialogDescription>
                {camera.campus_name} · {camera.nvr_name} · channel {camera.channel_number} · main stream {streamLabel(camera.main_stream)}
              </DialogDescription>
            </DialogHeader>
            <LiveCameraPlayer camera={camera} stream="main" large />
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
