import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { BackupPolicy, DestinationOption, RecordingMode } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useBulkPolicy, useSetPolicy } from './api'
import { type Draft, draftToInput, initial, KEEP } from './policyDraft'

const FREQUENCIES = [
  { value: 15, label: 'Every 15 minutes' },
  { value: 30, label: 'Every 30 minutes' },
  { value: 60, label: 'Every hour' },
  { value: 180, label: 'Every 3 hours' },
  { value: 360, label: 'Every 6 hours' },
  { value: 720, label: 'Every 12 hours' },
  { value: 1440, label: 'Once a day' },
]

const MODES: { value: RecordingMode; label: string }[] = [
  { value: 'all', label: 'All recordings' },
  { value: 'continuous', label: 'Continuous (scheduled) only' },
  { value: 'motion', label: 'Motion / alarm events only' },
  { value: 'event', label: 'Other events only' },
]

function Form({
  policy,
  cameraIds,
  destinations,
  onDone,
}: {
  policy?: BackupPolicy
  cameraIds: string[]
  destinations: DestinationOption[]
  onDone: () => void
}) {
  const bulk = !policy
  const [d, setD] = useState<Draft>(() => initial(policy, bulk))
  const [error, setError] = useState<string | null>(null)
  const set = useSetPolicy()
  const bulkSet = useBulkPolicy()
  const pending = set.isPending || bulkSet.isPending
  const patch = (p: Partial<Draft>) => setD((s) => ({ ...s, ...p }))
  const keep = bulk ? [{ value: KEEP, label: 'Keep each camera’s current setting' }] : []
  const turningOn = d.enabled === 'on' && (!policy || !policy.enabled)

  const submit = async () => {
    const { input, error: problem } = draftToInput(d)
    if (problem) return setError(problem)
    setError(null)
    try {
      if (policy) await set.mutateAsync({ cameraId: policy.camera_id, ...input })
      else await bulkSet.mutateAsync({ camera_ids: cameraIds, ...input })
      toast.success(policy ? 'Backup settings saved' : `Updated ${cameraIds.length} cameras`)
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const row = (id: string, label: string, value: string, onChange: (v: string) => void, options: { value: string | number; label: string }[]) => (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {[...keep, ...options].map((o) => (
            <SelectItem key={o.value} value={String(o.value)}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )

  return (
    <div className="space-y-4">
      {row('pol-enabled', 'Automatic backup', d.enabled, (v) => patch({ enabled: v }), [
        { value: 'on', label: 'On' },
        { value: 'off', label: 'Off' },
      ])}
      {row(
        'pol-dest',
        'Destination',
        d.destination,
        (v) => patch({ destination: v }),
        destinations.filter((x) => x.enabled).map((x) => ({ value: x.id, label: x.name })),
      )}
      {row('pol-stream', 'Which recording to back up', d.stream, (v) => patch({ stream: v }), [
        { value: 'sub', label: 'Substream (smaller, about 230 MB per hour)' },
        { value: 'main', label: 'Main stream (full quality, about 900 MB per hour)' },
      ])}
      {row(
        'pol-freq',
        'How often',
        d.frequency,
        (v) => patch({ frequency: v }),
        FREQUENCIES.map((f) => ({ value: f.value, label: f.label })),
      )}
      {row('pol-mode', 'Recording types', d.mode, (v) => patch({ mode: v }), MODES)}
      {(turningOn || bulk) && (
        <div>
          <Label htmlFor="pol-backfill">When first turned on, also back up the last (hours)</Label>
          <Input id="pol-backfill" type="number" min={0} max={168} inputMode="numeric" value={d.backfill} onChange={(e) => patch({ backfill: e.target.value })} />
          <p className="mt-1 text-xs text-muted-foreground">0 means only recordings made from now on. Up to 168 hours (7 days). Only used when the policy is switched on.</p>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button onClick={() => void submit()} disabled={pending}>
          {pending ? 'Saving…' : 'Save'}
        </Button>
      </DialogFooter>
    </div>
  )
}

export function PolicyDialog({
  open,
  onOpenChange,
  policy,
  cameraIds,
  destinations,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  /** One camera's policy, or undefined for a bulk edit of `cameraIds`. */
  policy?: BackupPolicy
  cameraIds: string[]
  destinations: DestinationOption[]
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{policy ? `Backup settings: ${policy.camera_name}` : `Backup settings for ${cameraIds.length} cameras`}</DialogTitle>
          <DialogDescription>
            {policy
              ? `${policy.campus_name} · ${policy.nvr_name} · channel ${policy.channel_number}`
              : 'Only the settings you change are applied. Everything else stays as it is on each camera.'}
          </DialogDescription>
        </DialogHeader>
        {open && <Form key={policy?.camera_id ?? 'bulk'} policy={policy} cameraIds={cameraIds} destinations={destinations} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}
