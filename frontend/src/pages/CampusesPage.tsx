import { Building2, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { Campus } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { isAdmin, useAuth } from '@/features/auth/store'
import { useCampuses, useDeleteCampus } from '@/features/campuses/api'
import { CampusFormDialog } from '@/features/campuses/CampusFormDialog'

export function CampusesPage() {
  const admin = isAdmin(useAuth((s) => s.user))
  const [q, setQ] = useState('')
  const { data, isLoading, error, refetch } = useCampuses({ q: q || undefined })
  const del = useDeleteCampus()
  const [editing, setEditing] = useState<Campus | undefined>()
  const [formOpen, setFormOpen] = useState(false)
  const [deleting, setDeleting] = useState<Campus | undefined>()

  const openForm = (c?: Campus) => {
    setEditing(c)
    setFormOpen(true)
  }

  return (
    <>
      <PageHeader
        title="Campuses"
        description="Each campus is a physical site with its own network and NVRs"
        actions={
          admin && (
            <Button onClick={() => openForm()}>
              <Plus /> Add campus
            </Button>
          )
        }
      />

      <div className="mb-4 max-w-sm">
        <Input placeholder="Search campuses…" aria-label="Search campuses" value={q} onChange={(e) => setQ(e.target.value)} />
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
        <EmptyState icon={<Building2 className="size-8" />} title={q ? 'No campuses match your search' : 'No campuses yet'}>
          {!q && (admin ? 'Add your first campus to start registering NVRs.' : 'You have not been given access to any campus yet. Ask an administrator.')}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Location</TableHead>
                <TableHead className="text-right">NVRs</TableHead>
                <TableHead className="text-right">Cameras</TableHead>
                <TableHead className="text-right">Backup enabled</TableHead>
                {admin && <TableHead className="w-24 text-right">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <Link to={`/nvrs?campus=${c.id}`} className="font-medium hover:underline">
                      {c.name}
                    </Link>
                    {c.description && <p className="line-clamp-1 text-xs text-muted-foreground">{c.description}</p>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{c.location || '—'}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {c.nvr_count}
                    {c.nvr_count > 0 && <span className="ml-1 text-xs text-muted-foreground">({c.connected_nvr_count} connected)</span>}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{c.camera_count}</TableCell>
                  <TableCell className="text-right tabular-nums">{c.backup_enabled_camera_count}</TableCell>
                  {admin && (
                    <TableCell className="text-right">
                      <Button variant="ghost" size="icon" aria-label={`Edit ${c.name}`} onClick={() => openForm(c)}>
                        <Pencil />
                      </Button>
                      <Button variant="ghost" size="icon" aria-label={`Delete ${c.name}`} onClick={() => setDeleting(c)}>
                        <Trash2 />
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <CampusFormDialog open={formOpen} onOpenChange={setFormOpen} campus={editing} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(undefined)}
        title={`Delete "${deleting?.name}"?`}
        description="The campus is removed from the app. A campus that still has NVRs cannot be deleted; remove or move them first."
        pending={del.isPending}
        onConfirm={() =>
          deleting &&
          del.mutate(deleting.id, {
            onSuccess: () => {
              toast.success('Campus deleted')
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
