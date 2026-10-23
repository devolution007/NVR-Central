import { Info, TriangleAlert } from 'lucide-react'
import type { BackupFileStatus, BackupJobStatus } from '@/api/types'
import { cn } from '@/lib/utils'
import { useBackupStatus } from './api'

const JOB: Record<BackupJobStatus, { label: string; cls: string; hint: string }> = {
  queued: { label: 'Queued', cls: 'text-muted-foreground', hint: 'Waiting for a worker, or for another backup on the same NVR to finish.' },
  running: { label: 'Running', cls: 'text-primary', hint: 'Copying recordings now.' },
  success: { label: 'Done', cls: 'text-success', hint: 'Every recording in the window is on the SFTP server.' },
  partial: { label: 'Partly done', cls: 'text-warning', hint: 'Some recordings are backed up and some failed. The next run retries them.' },
  failed: { label: 'Failed', cls: 'text-destructive', hint: 'Nothing could be backed up in this run.' },
  cancelled: { label: 'Cancelled', cls: 'text-muted-foreground', hint: 'Stopped before finishing.' },
}

const FILE: Record<BackupFileStatus, { label: string; cls: string }> = {
  pending: { label: 'Waiting', cls: 'text-muted-foreground' },
  downloading: { label: 'Downloading from NVR', cls: 'text-primary' },
  uploading: { label: 'Uploading to SFTP', cls: 'text-primary' },
  verifying: { label: 'Verifying', cls: 'text-primary' },
  done: { label: 'Backed up', cls: 'text-success' },
  failed: { label: 'Failed', cls: 'text-destructive' },
  skipped: { label: 'Skipped', cls: 'text-muted-foreground' },
  cancelled: { label: 'Cancelled', cls: 'text-muted-foreground' },
}

export const JobStatusBadge = ({ status }: { status: BackupJobStatus }) => (
  <span className={cn('inline-flex items-center gap-2 text-sm font-medium', JOB[status].cls)} title={JOB[status].hint}>
    <span className={cn('size-2 rounded-full bg-current', status === 'running' && 'animate-pulse')} aria-hidden />
    {JOB[status].label}
  </span>
)

export const FileStatusBadge = ({ status }: { status: BackupFileStatus }) => (
  <span className={cn('inline-flex items-center gap-2 text-sm', FILE[status].cls)}>
    <span className="size-2 rounded-full bg-current" aria-hidden />
    {FILE[status].label}
  </span>
)

/** Tells the user when nothing is going to run their backups, instead of leaving jobs "queued" without explanation. */
export function WorkerNotice() {
  const { data } = useBackupStatus()
  if (!data) return null
  if (data.worker_online === true && data.destinations_failing === 0) return null
  return (
    <div role="alert" className="mb-4 flex gap-2 rounded-lg border border-warning/50 bg-warning/10 p-3 text-sm">
      {data.worker_online === true ? <Info className="mt-0.5 size-4 shrink-0" aria-hidden /> : <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />}
      <div className="space-y-1">
        {data.worker_online !== true && (
          <p>
            <strong>Backups will not run: </strong>
            {data.detail}
          </p>
        )}
        {data.destinations_failing > 0 && <p>{data.destinations_failing} SFTP destination(s) failed their last test. Check the SFTP Destinations page.</p>}
      </div>
    </div>
  )
}
