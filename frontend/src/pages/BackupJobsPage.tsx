import { Activity } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { errorMessage } from '@/api/client'
import type { BackupJobStatus } from '@/api/types'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useJobs } from '@/features/backups/api'
import { JobDetailDialog, JobProgress } from '@/features/backups/JobDetailDialog'
import { JobStatusBadge, WorkerNotice } from '@/features/backups/StatusBadges'
import { useCampuses } from '@/features/campuses/api'
import { formatBytes, formatDateTime, timeAgo } from '@/lib/format'

const ALL = 'all'
const PAGE_SIZE = 25

function JobsTable({ mode }: { mode: 'active' | 'history' }) {
  const [params, setParams] = useSearchParams()
  const status = (params.get('status') as BackupJobStatus | null) ?? undefined
  const campus = params.get('campus') ?? ALL
  const trigger = (params.get('trigger') as 'scheduled' | 'manual' | null) ?? undefined
  const page = Number(params.get('page') ?? '1') || 1
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value === ALL) next.delete(key)
    else next.set(key, value)
    next.delete('page')
    setParams(next, { replace: true })
  }
  const campuses = useCampuses({ page_size: 200 })
  const jobs = useJobs({
    active: mode === 'active' || undefined,
    status: mode === 'history' ? status : undefined,
    campus_id: campus === ALL ? undefined : campus,
    trigger: mode === 'history' ? trigger : undefined,
    page,
    page_size: PAGE_SIZE,
  })
  const [open, setOpen] = useState<string | undefined>()
  const rows = jobs.data?.items ?? []
  const pages = Math.max(1, Math.ceil((jobs.data?.total ?? 0) / PAGE_SIZE))

  return (
    <>
      <WorkerNotice />
      <div className="mb-4 flex flex-wrap gap-3">
        <div className="w-full sm:w-56">
          <Select value={campus} onValueChange={(v) => set('campus', v)}>
            <SelectTrigger className="w-full" aria-label="Filter by campus">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All campuses</SelectItem>
              {campuses.data?.items.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {mode === 'history' && (
          <>
            <div className="w-full sm:w-44">
              <Select value={status ?? ALL} onValueChange={(v) => set('status', v)}>
                <SelectTrigger className="w-full" aria-label="Filter by result">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Any result</SelectItem>
                  <SelectItem value="success">Done</SelectItem>
                  <SelectItem value="partial">Partly done</SelectItem>
                  <SelectItem value="failed">Failed</SelectItem>
                  <SelectItem value="cancelled">Cancelled</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="w-full sm:w-44">
              <Select value={trigger ?? ALL} onValueChange={(v) => set('trigger', v)}>
                <SelectTrigger className="w-full" aria-label="Filter by how it started">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Scheduled and manual</SelectItem>
                  <SelectItem value="scheduled">Scheduled</SelectItem>
                  <SelectItem value="manual">Manual</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </>
        )}
      </div>

      {jobs.error ? (
        <ErrorState message={errorMessage(jobs.error)} onRetry={() => void jobs.refetch()} />
      ) : jobs.isLoading ? (
        <Skeleton className="h-40" />
      ) : rows.length === 0 ? (
        <EmptyState icon={<Activity className="size-8" />} title={mode === 'active' ? 'No backups running' : 'No backups yet'}>
          {mode === 'active'
            ? 'Running and queued backups show here with live progress.'
            : 'Finished backups, scheduled and manual, are listed here.'}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Camera</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Progress</TableHead>
                <TableHead>Recordings</TableHead>
                <TableHead>Destination</TableHead>
                <TableHead>{mode === 'active' ? 'Started' : 'Finished'}</TableHead>
                <TableHead className="w-20" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((j) => (
                <TableRow key={j.id}>
                  <TableCell>
                    <span className="font-medium">{j.camera_name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {j.campus_name} · {j.nvr_name} · {j.stream === 'main' ? 'main' : 'sub'} · {j.trigger === 'scheduled' ? 'scheduled' : 'manual'}
                    </span>
                  </TableCell>
                  <TableCell>
                    <JobStatusBadge status={j.status} />
                    {j.message && j.status !== 'success' && j.status !== 'running' && (
                      <span className="block max-w-xs truncate text-xs text-muted-foreground" title={j.message}>
                        {j.message}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>{j.bytes_total > 0 ? <JobProgress job={j} /> : <span className="text-muted-foreground">{formatBytes(j.bytes_done)}</span>}</TableCell>
                  <TableCell className="tabular-nums text-sm">
                    {j.files_done}/{j.files_total}
                    {j.files_failed > 0 && <span className="ml-1 text-destructive">({j.files_failed} failed)</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{j.destination_name}</TableCell>
                  <TableCell className="text-muted-foreground" title={formatDateTime(mode === 'active' ? j.started_at : j.finished_at)}>
                    {timeAgo(mode === 'active' ? (j.started_at ?? j.created_at) : (j.finished_at ?? j.created_at))}
                  </TableCell>
                  <TableCell>
                    <Button variant="ghost" size="sm" onClick={() => setOpen(j.id)} aria-label={`Details of the ${j.camera_name} backup`}>
                      Details
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {pages > 1 && (
        <div className="mt-3 flex items-center justify-end gap-2 text-sm">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setParams({ ...Object.fromEntries(params), page: String(page - 1) })}>
            Previous
          </Button>
          <span className="tabular-nums text-muted-foreground">
            Page {page} of {pages}
          </span>
          <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setParams({ ...Object.fromEntries(params), page: String(page + 1) })}>
            Next
          </Button>
        </div>
      )}
      <JobDetailDialog jobId={open} onClose={() => setOpen(undefined)} />
    </>
  )
}

export function BackupJobsPage() {
  return (
    <>
      <PageHeader title="Backup Jobs" description="Backups that are queued or running right now (updates every 2 seconds)" />
      <JobsTable mode="active" />
    </>
  )
}

export function BackupHistoryPage() {
  return (
    <>
      <PageHeader title="Backup History" description="Every backup that has run, scheduled or manual" />
      <JobsTable mode="history" />
    </>
  )
}
