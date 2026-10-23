import { useEffect } from 'react'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useCampuses } from '@/features/campuses/api'
import { useCameras } from '@/features/nvrs/api'

/** Campus then camera. Controlled by the parent through the two ids. */
export function CameraPicker({
  campusId,
  cameraId,
  onChange,
}: {
  campusId: string
  cameraId: string
  onChange: (next: { campusId: string; cameraId: string }) => void
}) {
  const campuses = useCampuses({ page_size: 200 })
  const cameras = useCameras({ campus_id: campusId || undefined, page_size: 500 })
  const list = (cameras.data?.items ?? []).filter((c) => c.status !== 'unavailable')

  // With a single campus there is nothing to choose: select it.
  const only = campuses.data?.items.length === 1 ? campuses.data.items[0] : undefined
  useEffect(() => {
    if (only && !campusId) onChange({ campusId: only.id, cameraId: '' })
  }, [only, campusId, onChange])

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div>
        <Label htmlFor="pick-campus">Campus</Label>
        <Select value={campusId} onValueChange={(v) => onChange({ campusId: v, cameraId: '' })}>
          <SelectTrigger id="pick-campus" className="w-full">
            <SelectValue placeholder="Choose a campus" />
          </SelectTrigger>
          <SelectContent>
            {campuses.data?.items.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div>
        <Label htmlFor="pick-camera">Camera</Label>
        <Select value={cameraId} onValueChange={(v) => onChange({ campusId, cameraId: v })} disabled={!campusId}>
          <SelectTrigger id="pick-camera" className="w-full">
            <SelectValue placeholder={campusId ? 'Choose a camera' : 'Choose a campus first'} />
          </SelectTrigger>
          <SelectContent>
            {list.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                {c.name} · {c.nvr_name} · ch {c.channel_number}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
