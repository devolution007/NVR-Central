import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { BackupStream } from '@/api/types'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAuth } from '@/features/auth/store'
import { useCreateJob, useDestinationOptions } from '@/features/backups/api'
import { CameraPicker } from '@/features/backups/CameraPicker'
import { localInputToIso, toLocalInput } from '@/features/backups/datetime'
import { WorkerNotice } from '@/features/backups/StatusBadges'

const HOUR = 3_600_000

export function ManualBackupPage() {
  const role = useAuth((s) => s.user?.role)
  const navigate = useNavigate()
  const [campusId, setCampusId] = useState('')
  const [cameraId, setCameraId] = useState('')
  const [stream, setStream] = useState<BackupStream>('sub')
  const [from, setFrom] = useState(() => toLocalInput(new Date(Date.now() - 6 * HOUR)))
  const [to, setTo] = useState(() => toLocalInput(new Date()))
  const [destination, setDestination] = useState('')
  const [error, setError] = useState<string | null>(null)
  const destinations = useDestinationOptions()
  const create = useCreateJob()
  const options = (destinations.data ?? []).filter((d) => d.enabled)

  if (role === 'viewer')
    return (
      <>
        <PageHeader title="Manual Backup" />
        <p className="text-sm text-muted-foreground">Viewers cannot start backups. Ask an operator or administrator.</p>
      </>
    )

  const submit = () => {
    setError(null)
    if (!cameraId) return setError('Choose a camera')
    const start = new Date(from)
    const end = new Date(to)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) return setError('The end must be after the start')
    if (end.getTime() - start.getTime() > 7 * 24 * HOUR) return setError('A manual backup covers at most 7 days')
    if (!destination) return setError('Choose where to back up to')
    create.mutate(
      { camera_id: cameraId, stream, destination_id: destination, start: localInputToIso(from), end: localInputToIso(to) },
      {
        onSuccess: () => {
          toast.success('Backup queued')
          navigate('/backup/jobs')
        },
        onError: (e) => setError(errorMessage(e)),
      },
    )
  }

  return (
    <>
      <PageHeader title="Manual Backup" description="Copy everything one camera recorded in a time range to an SFTP server" />
      <WorkerNotice />
      <div className="max-w-2xl space-y-4 rounded-lg border p-4">
        <CameraPicker campusId={campusId} cameraId={cameraId} onChange={(n) => (setCampusId(n.campusId), setCameraId(n.cameraId))} />
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="man-from">From (your time zone)</Label>
            <Input id="man-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="man-to">To</Label>
            <Input id="man-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="man-stream">Which recording</Label>
            <Select value={stream} onValueChange={(v) => setStream(v as BackupStream)}>
              <SelectTrigger id="man-stream" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="sub">Substream (about 230 MB per hour)</SelectItem>
                <SelectItem value="main">Main stream (about 900 MB per hour)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="man-dest">Back up to</Label>
            <Select value={destination} onValueChange={setDestination}>
              <SelectTrigger id="man-dest" className="w-full">
                <SelectValue placeholder={options.length ? 'Choose a destination' : 'No destination available'} />
              </SelectTrigger>
              <SelectContent>
                {options.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Recordings already backed up to the same destination are not copied again. Recordings still being written are skipped.
        </p>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button onClick={submit} disabled={create.isPending}>
          {create.isPending ? 'Starting…' : 'Start backup'}
        </Button>
      </div>
    </>
  )
}
