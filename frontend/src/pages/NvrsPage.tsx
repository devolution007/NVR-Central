import { Info, Pencil, Plus, Server, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { Nvr } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { NvrStatusIndicator } from '@/components/NvrStatusIndicator'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { isAdmin, useAuth } from '@/features/auth/store'
import { useCampuses } from '@/features/campuses/api'
import { useDeleteNvr, useNvrs } from '@/features/nvrs/api'
import { NvrActions } from '@/features/nvrs/NvrActions'
import { NvrFormDialog } from '@/features/nvrs/NvrFormDialog'
import { timeAgo } from '@/lib/format'

const ALL = 'all'
const TYPE_LABEL = { tunnel: 'Tunnel', lan: 'LAN', p2p: 'P2P' } as const

export function NvrsPage() {
  const admin = isAdmin(useAuth((s) => s.user))
  const [params, setParams] = useSearchParams()
  const campusFilter = params.get('campus') ?? ALL
  const [q, setQ] = useState('')

  const campuses = useCampuses({ page_size: 200 })
  const { data, isLoading, error, refetch } = useNvrs({ campus_id: campusFilter === ALL ? undefined : campusFilter, q: q || undefined })
  const del = useDeleteNvr()

  const [editing, setEditing] = useState<Nvr | undefined>()
  const [formOpen, setFormOpen] = useState(false)
  const [deleting, setDeleting] = useState<Nvr | undefined>()
  const campusList = campuses.data?.items ?? []

  const openForm = (n?: Nvr) => {
    setEditing(n)
    setFormOpen(true)
  }

  return (
    <>
      <PageHeader
        title="NVR Management"
        description="Register the recorders at each campus"
        actions={
          admin && (
            <Button onClick={() => openForm()} disabled={campusList.length === 0} title={campusList.length === 0 ? 'Add a campus first' : undefined}>
              <Plus /> Register NVR
            </Button>
          )
        }
      />

      <div role="note" className="mb-4 flex gap-2 rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        <p>
          A new NVR is checked automatically after you save it. <strong className="text-foreground">Not tested</strong> means no check has run, which is not
          the same as offline. NVRs that rejected their credentials are not re-checked automatically, to avoid locking the NVR account.
        </p>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="w-full sm:w-56">
          <Select
            value={campusFilter}
            onValueChange={(v) => {
              const next = new URLSearchParams(params)
              if (v === ALL) next.delete('campus')
              else next.set('campus', v)
              setParams(next, { replace: true })
            }}
          >
            <SelectTrigger className="w-full" aria-label="Filter by campus">
              <SelectValue placeholder="All campuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All campuses</SelectItem>
              {campusList.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="w-full sm:w-64">
          <Input placeholder="Search NVRs…" aria-label="Search NVRs" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {error ? (
        <ErrorState message={errorMessage(error)} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : data && data.items.length === 0 ? (
        <EmptyState icon={<Server className="size-8" />} title={q || campusFilter !== ALL ? 'No NVRs match these filters' : 'No NVRs registered'}>
          {admin && campusList.length === 0
            ? 'Add a campus first, then register its NVRs.'
            : admin
              ? 'Register an NVR to begin.'
              : 'There are no NVRs in the campuses you can access.'}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Campus</TableHead>
                <TableHead>Connection</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Last checked</TableHead>
                <TableHead className="text-right">Cameras</TableHead>
                {admin && <TableHead className="w-60 text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.items.map((n) => (
                <TableRow key={n.id} className={n.enabled ? undefined : 'opacity-60'}>
                  <TableCell className="font-medium">
                    <Link to={`/nvrs/${n.id}`} className="hover:underline">
                      {n.name}
                    </Link>
                    {!n.enabled && <span className="ml-2 text-xs font-normal text-muted-foreground">(disabled)</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{n.campus_name}</TableCell>
                  <TableCell>
                    <span className="text-sm">{TYPE_LABEL[n.connection_type]}</span>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">
                      {n.connection_type === 'p2p' ? n.p2p_serial_masked : `${n.host}:${n.http_port}`}
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{n.model ?? '—'}</TableCell>
                  <TableCell>
                    <NvrStatusIndicator status={n.status} message={n.status_message} />
                  </TableCell>
                  <TableCell className="text-muted-foreground" title={n.last_checked_at ?? undefined}>
                    {timeAgo(n.last_checked_at)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{n.camera_count}</TableCell>
                  {admin && (
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <NvrActions nvr={n} />
                        <Button variant="ghost" size="icon" aria-label={`Edit ${n.name}`} onClick={() => openForm(n)}>
                          <Pencil />
                        </Button>
                        <Button variant="ghost" size="icon" aria-label={`Delete ${n.name}`} onClick={() => setDeleting(n)}>
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <NvrFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        nvr={editing}
        campuses={campusList}
        defaultCampusId={campusFilter === ALL ? undefined : campusFilter}
      />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(undefined)}
        title={`Delete "${deleting?.name}"?`}
        description="The NVR is removed from the app. Its cameras and backup history are kept for the record."
        pending={del.isPending}
        onConfirm={() =>
          deleting &&
          del.mutate(deleting.id, {
            onSuccess: () => {
              toast.success('NVR deleted')
              setDeleting(undefined)
            },
            onError: (err) => {
              toast.error(errorMessage(err))
              setDeleting(undefined)
            },
          })
        }
      />
    </>
  )
}
