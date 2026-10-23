import { Film, Loader2, Search } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { RecordingItem } from '@/api/types'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/features/auth/store'
import { useCreateJob, useDestinationOptions, useRecordings } from '@/features/backups/api'
import { CameraPicker } from '@/features/backups/CameraPicker'
import { localInputToIso, toLocalInput } from '@/features/backups/datetime'
import { FileStatusBadge, WorkerNotice } from '@/features/backups/StatusBadges'
import { formatBytes, utcOffsetLabel } from '@/lib/format'

const day = (ms: number) => toLocalInput(new Date(Date.now() - ms))
const HOUR = 3_600_000

export function RecordingsPage() {
  const user = useAuth((s) => s.user)
  const canBackup = user?.role === 'admin' || user?.role === 'operator'
  const [campusId, setCampusId] = useState('')
  const [cameraId, setCameraId] = useState('')
  const [from, setFrom] = useState(() => day(24 * HOUR))
  const [to, setTo] = useState(() => toLocalInput(new Date()))
  const [destination, setDestination] = useState('')
  const [submitted, setSubmitted] = useState<{ cameraId: string; start: string; end: string } | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const destinations = useDestinationOptions()
  const search = useRecordings(submitted?.cameraId, submitted?.start ?? '', submitted?.end ?? '', destination || undefined)
  const create = useCreateJob()
  const activeDests = (destinations.data ?? []).filter((d) => d.enabled)

  const runSearch = () => {
    setFormError(null)
    if (!cameraId) return setFormError('Choose a camera')
    const start = new Date(from)
    const end = new Date(to)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) return setFormError('The end must be after the start')
    if (end.getTime() - start.getTime() > 48 * HOUR) return setFormError('Search up to 48 hours at a time')
    setSubmitted({ cameraId, start: localInputToIso(from), end: localInputToIso(to) })
  }

  const startBackup = (item: { start: string; end: string; stream: 'main' | 'sub' }, label: string) => {
    if (!submitted) return
    create.mutate(
      { camera_id: submitted.cameraId, stream: item.stream, destination_id: destination || undefined, start: item.start, end: item.end },
      {
        onSuccess: () => toast.success(`${label} queued. Follow it on Backup Jobs.`),
        onError: (e) => toast.error(errorMessage(e)),
      },
    )
  }

  const items = search.data?.items ?? []
  const todo = items.filter((i) => !i.in_progress && i.backup_status !== 'done')

  const backupCell = (i: RecordingItem) => {
    if (i.in_progress) return <span className="text-muted-foreground">Still being recorded</span>
    if (i.backup_status) return <FileStatusBadge status={i.backup_status} />
    return <span className="text-muted-foreground">Not backed up</span>
  }

  return (
    <>
      <PageHeader title="Recording Browser" description="Look up what the NVR has recorded and back it up" />
      <WorkerNotice />

      <div className="mb-4 space-y-4 rounded-lg border p-4">
        <CameraPicker campusId={campusId} cameraId={cameraId} onChange={(n) => (setCampusId(n.campusId), setCameraId(n.cameraId))} />
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <Label htmlFor="rec-from">From (your time zone)</Label>
            <Input id="rec-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="rec-to">To</Label>
            <Input id="rec-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="rec-dest">Back up to</Label>
            <Select value={destination} onValueChange={setDestination}>
              <SelectTrigger id="rec-dest" className="w-full">
                <SelectValue placeholder="The camera's own destination" />
              </SelectTrigger>
              <SelectContent>
                {activeDests.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        {formError && (
          <p role="alert" className="text-sm text-destructive">
            {formError}
          </p>
        )}
        <Button onClick={runSearch} disabled={search.isFetching}>
          {search.isFetching ? <Loader2 className="animate-spin" /> : <Search />} Search the NVR
        </Button>
      </div>

      {!submitted ? (
        <EmptyState icon={<Film className="size-8" />} title="Choose a camera and a time range">
          The NVR is asked for its recording files in that window. Nothing is copied until you press Back up.
        </EmptyState>
      ) : search.isFetching && !search.data ? (
        <Skeleton className="h-40" />
      ) : search.error ? (
        <ErrorState message={errorMessage(search.error)} onRetry={() => void search.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState icon={<Film className="size-8" />} title="No recordings in that window">
          The NVR reports nothing recorded for this camera between those times.
        </EmptyState>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              {items.length} recording files · NVR clock {utcOffsetLabel(search.data?.utc_offset_minutes)} · times in your time zone
            </p>
            {canBackup && todo.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                disabled={create.isPending}
                onClick={() => {
                  // One job per stream present: a job backs up a single stream.
                  const streams = [...new Set(todo.map((t) => t.stream))]
                  for (const stream of streams) {
                    const part = todo.filter((t) => t.stream === stream)
                    const first = part[0]
                    const last = part[part.length - 1]
                    if (first && last) startBackup({ start: first.start, end: last.end, stream }, `Backup of ${part.length} ${stream === 'main' ? 'main stream' : 'substream'} recordings`)
                  }
                }}
              >
                Back up everything not yet backed up ({todo.length})
              </Button>
            )}
          </div>
          <div className="overflow-x-auto rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Stream</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead>Backup</TableHead>
                  {canBackup && <TableHead className="w-28" />}
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((i) => (
                  <TableRow key={`${i.stream}-${i.start}`}>
                    <TableCell className="whitespace-nowrap">
                      {new Date(i.start).toLocaleString()} – {new Date(i.end).toLocaleTimeString()}
                    </TableCell>
                    <TableCell>{i.stream === 'main' ? 'Main' : 'Sub'}</TableCell>
                    <TableCell className="text-muted-foreground">{i.kind || '—'}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatBytes(i.size_bytes)}</TableCell>
                    <TableCell>{backupCell(i)}</TableCell>
                    {canBackup && (
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={i.in_progress || i.backup_status === 'done' || create.isPending}
                          onClick={() => startBackup(i, 'Backup')}
                          aria-label={`Back up the recording from ${new Date(i.start).toLocaleTimeString()}`}
                        >
                          Back up
                        </Button>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Recordings still being written are skipped until they are complete. Progress and results are on{' '}
            <Link to="/backup/jobs" className="underline underline-offset-4">
              Backup Jobs
            </Link>
            .
          </p>
        </>
      )}
    </>
  )
}
