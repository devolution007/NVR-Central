import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { BackupJob } from '@/api/types'
import { ErrorState } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatBytes, formatDateTime } from '@/lib/format'
import { isActiveJob, useCancelJob, useJob, useRetryJob } from './api'
import { progressPercent } from './progress'
import { FileStatusBadge, JobStatusBadge } from './StatusBadges'

export function JobProgress({ job }: { job: Pick<BackupJob, 'bytes_done' | 'bytes_total' | 'status'> }) {
  const pct = progressPercent(job.bytes_done, job.bytes_total)
  return (
    <div className="min-w-32">
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Backup progress"
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
      <span className="mt-1 block text-xs tabular-nums text-muted-foreground">
        {formatBytes(job.bytes_done)} of {formatBytes(job.bytes_total)}
      </span>
    </div>
  )
}

export function JobDetailDialog({ jobId, onClose }: { jobId: string | undefined; onClose: () => void }) {
  const { data: job, error, isLoading } = useJob(jobId)
  const cancel = useCancelJob()
  const retry = useRetryJob()

  return (
    <Dialog open={!!jobId} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Backup job</DialogTitle>
          <DialogDescription>{job ? `${job.camera_name} · ${job.nvr_name} · ${job.stream === 'main' ? 'main stream' : 'substream'} → ${job.destination_name}` : 'Loading…'}</DialogDescription>
        </DialogHeader>
        {error ? (
          <ErrorState message={errorMessage(error)} />
        ) : isLoading || !job ? (
          <Skeleton className="h-40" />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <p className="text-xs text-muted-foreground">Status</p>
                <JobStatusBadge status={job.status} />
                {job.cancel_requested && isActiveJob(job.status) && <span className="block text-xs text-muted-foreground">Cancelling…</span>}
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Recordings</p>
                {job.files_done} of {job.files_total} done
                {job.files_failed > 0 && <span className="text-destructive"> · {job.files_failed} failed</span>}
                {job.files_skipped > 0 && <span className="block text-xs text-muted-foreground">{job.files_skipped} still being recorded</span>}
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Started by</p>
                {job.trigger === 'scheduled' ? 'Schedule' : (job.requested_by ?? 'Manual')}
              </div>
            </div>
            <JobProgress job={job} />
            <p className="text-xs text-muted-foreground">
              Window {formatDateTime(job.window_start)} to {formatDateTime(job.window_end)} · created {formatDateTime(job.created_at)}
              {job.finished_at && ` · finished ${formatDateTime(job.finished_at)}`}
            </p>
            {job.message && (
              <p className="rounded-md bg-muted p-2 text-sm" role="status">
                {job.message}
              </p>
            )}

            {job.files.length > 0 && (
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Recording (UTC window)</TableHead>
                      <TableHead>Size</TableHead>
                      <TableHead>State</TableHead>
                      <TableHead>On the server</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {job.files.map((f) => (
                      <TableRow key={f.id}>
                        <TableCell className="whitespace-nowrap text-xs">
                          {formatDateTime(f.recording_start)} – {new Date(f.recording_end).toLocaleTimeString()}
                        </TableCell>
                        <TableCell className="tabular-nums text-sm">{formatBytes(f.size_bytes)}</TableCell>
                        <TableCell>
                          <FileStatusBadge status={f.status} />
                          {f.attempts > 1 && <span className="block text-xs text-muted-foreground">attempt {f.attempts}</span>}
                          {f.error && (
                            <span className="block max-w-xs text-xs text-destructive" title={f.error}>
                              {f.error}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="max-w-xs break-all font-mono text-[11px] text-muted-foreground">{f.remote_path ?? '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}

            <div className="flex justify-end gap-2">
              {isActiveJob(job.status) ? (
                <Button
                  variant="outline"
                  disabled={cancel.isPending || job.cancel_requested}
                  onClick={() => cancel.mutate(job.id, { onError: (e) => toast.error(errorMessage(e)) })}
                >
                  Cancel job
                </Button>
              ) : (
                job.status !== 'success' && (
                  <Button
                    variant="outline"
                    disabled={retry.isPending}
                    onClick={() =>
                      retry.mutate(job.id, {
                        onSuccess: () => {
                          toast.success('Started again')
                          onClose()
                        },
                        onError: (e) => toast.error(errorMessage(e)),
                      })
                    }
                  >
                    Try this window again
                  </Button>
                )
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
