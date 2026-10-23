import { DatabaseBackup, Pencil } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { errorMessage } from '@/api/client'
import type { BackupPolicy } from '@/api/types'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/features/auth/store'
import { useDestinationOptions, usePolicies } from '@/features/backups/api'
import { PolicyDialog } from '@/features/backups/PolicyDialog'
import { JobStatusBadge, WorkerNotice } from '@/features/backups/StatusBadges'
import { useCampuses } from '@/features/campuses/api'
import { formatDateTime, timeAgo } from '@/lib/format'

const ALL = 'all'
const FREQ = (m: number) => (m % 1440 === 0 ? `${m / 1440} d` : m % 60 === 0 ? `${m / 60} h` : `${m} min`)

export function BackupSettingsPage() {
  const user = useAuth((s) => s.user)
  const canEdit = user?.role === 'admin' || user?.role === 'operator'
  const [params, setParams] = useSearchParams()
  const campus = params.get('campus') ?? ALL
  const enabledFilter = params.get('enabled') ?? ALL
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value === ALL) next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: true })
  }

  const campuses = useCampuses({ page_size: 200 })
  const destinations = useDestinationOptions()
  const policies = usePolicies({
    campus_id: campus === ALL ? undefined : campus,
    enabled: enabledFilter === ALL ? undefined : enabledFilter === 'on',
    page_size: 500,
  })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<BackupPolicy | undefined>()
  const [bulkOpen, setBulkOpen] = useState(false)
  const [oneOpen, setOneOpen] = useState(false)
  const rows = policies.data?.items ?? []
  const dests = destinations.data ?? []

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.camera_id))

  return (
    <>
      <PageHeader title="Auto Backup Settings" description="Which cameras are backed up automatically, how often, and where to" />
      <WorkerNotice />

      {canEdit && !destinations.isLoading && dests.length === 0 && (
        <div role="note" className="mb-4 rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground">
          There is no SFTP destination yet. An administrator needs to add one on the SFTP Destinations page before backups can be turned on.
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="w-full sm:w-56">
          <Select value={campus} onValueChange={(v) => set('campus', v)}>
            <SelectTrigger className="w-full" aria-label="Filter by campus">
              <SelectValue placeholder="All campuses" />
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
        <div className="w-full sm:w-48">
          <Select value={enabledFilter} onValueChange={(v) => set('enabled', v)}>
            <SelectTrigger className="w-full" aria-label="Filter by backup state">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Any state</SelectItem>
              <SelectItem value="on">Backup on</SelectItem>
              <SelectItem value="off">Backup off</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {canEdit && (
          <Button variant="secondary" disabled={selected.size === 0} onClick={() => setBulkOpen(true)}>
            Edit {selected.size || ''} selected…
          </Button>
        )}
      </div>

      {policies.error ? (
        <ErrorState message={errorMessage(policies.error)} onRetry={() => void policies.refetch()} />
      ) : policies.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState icon={<DatabaseBackup className="size-8" />} title="No cameras to show">
          Cameras appear here after an NVR has been connected and its cameras discovered.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                {canEdit && (
                  <TableHead className="w-10">
                    <Checkbox
                      aria-label="Select all cameras"
                      checked={allSelected}
                      onCheckedChange={(c) => setSelected(c === true ? new Set(rows.map((r) => r.camera_id)) : new Set())}
                    />
                  </TableHead>
                )}
                <TableHead>Camera</TableHead>
                <TableHead>Automatic backup</TableHead>
                <TableHead>Destination</TableHead>
                <TableHead>What</TableHead>
                <TableHead>Backed up through</TableHead>
                <TableHead>Last run</TableHead>
                {canEdit && <TableHead className="w-12" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => (
                <TableRow key={p.camera_id}>
                  {canEdit && (
                    <TableCell>
                      <Checkbox aria-label={`Select ${p.camera_name}`} checked={selected.has(p.camera_id)} onCheckedChange={() => toggle(p.camera_id)} />
                    </TableCell>
                  )}
                  <TableCell>
                    <span className="font-medium">{p.camera_name}</span>
                    <span className="block text-xs text-muted-foreground">
                      {p.campus_name} · {p.nvr_name} · ch {p.channel_number}
                    </span>
                  </TableCell>
                  <TableCell>
                    {p.enabled ? (
                      <span className="text-success">On · every {FREQ(p.frequency_minutes)}</span>
                    ) : (
                      <span className="text-muted-foreground">{p.configured ? 'Off' : 'Not set up'}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{p.destination_name ?? '—'}</TableCell>
                  <TableCell className="text-sm">
                    {p.backup_stream === 'main' ? 'Main stream' : 'Substream'}
                    <span className="block text-xs text-muted-foreground">{p.recording_mode === 'all' ? 'All recordings' : p.recording_mode}</span>
                  </TableCell>
                  <TableCell className="text-muted-foreground" title={formatDateTime(p.checkpoint_at)}>
                    {p.enabled ? (p.checkpoint_at ? timeAgo(p.checkpoint_at) : 'not yet') : '—'}
                  </TableCell>
                  <TableCell>
                    {p.last_job_status ? (
                      <div>
                        <JobStatusBadge status={p.last_job_status} />
                        <span className="block text-xs text-muted-foreground" title={formatDateTime(p.last_run_at)}>
                          {timeAgo(p.last_run_at)}
                          {p.consecutive_failures > 0 && ` · ${p.consecutive_failures} failed in a row`}
                        </span>
                        {p.last_job_status !== 'success' && p.last_job_message && (
                          <span className="block max-w-xs truncate text-xs text-muted-foreground" title={p.last_job_message}>
                            {p.last_job_message}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="text-muted-foreground">Never</span>
                    )}
                  </TableCell>
                  {canEdit && (
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Edit backup settings of ${p.camera_name}`}
                        onClick={() => {
                          setEditing(p)
                          setOneOpen(true)
                        }}
                      >
                        <Pencil />
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <PolicyDialog open={oneOpen} onOpenChange={setOneOpen} policy={editing} cameraIds={editing ? [editing.camera_id] : []} destinations={dests} />
      <PolicyDialog
        open={bulkOpen}
        onOpenChange={(o) => {
          setBulkOpen(o)
          if (!o) setSelected(new Set())
        }}
        cameraIds={[...selected]}
        destinations={dests}
      />
    </>
  )
}
