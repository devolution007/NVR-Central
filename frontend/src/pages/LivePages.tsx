import { LayoutGrid, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { Layout } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useDeleteLayout, useLayouts } from '@/features/live/api'
import { LiveWall } from '@/features/live/LiveWall'
import { formatDateTime } from '@/lib/format'

export function LiveAllPage() {
  return (
    <>
      <PageHeader title="Live Monitoring" description="Cameras from all campuses. Substreams on the wall, the main stream in fullscreen." />
      <LiveWall source="all" />
    </>
  )
}

export function LiveFavoritesPage() {
  return (
    <>
      <PageHeader title="Favorite Cameras" description="Cameras you starred on the live wall" />
      <LiveWall source="favorites" />
    </>
  )
}

export function LiveLayoutsPage() {
  const layouts = useLayouts()
  const del = useDeleteLayout()
  const [deleting, setDeleting] = useState<Layout | undefined>()

  return (
    <>
      <PageHeader title="Saved Layouts" description="Your personal camera arrangements. Save one from the live wall." />
      {layouts.error ? (
        <ErrorState message={errorMessage(layouts.error)} onRetry={() => void layouts.refetch()} />
      ) : !layouts.data ? (
        <Skeleton className="h-32" />
      ) : layouts.data.length === 0 ? (
        <EmptyState icon={<LayoutGrid className="size-8" />} title="No saved layouts yet">
          Arrange the{' '}
          <Link to="/live" className="font-medium text-primary underline underline-offset-4">
            live wall
          </Link>{' '}
          the way you like, then use Saved layouts, Save current view.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead className="text-right">Cameras</TableHead>
                <TableHead className="text-right">Tiles</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="w-16 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {layouts.data.map((l) => (
                <TableRow key={l.id}>
                  <TableCell className="font-medium">
                    <Link to={`/live?layout=${l.id}&grid=${l.grid_size}`} className="hover:underline">
                      {l.name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{l.camera_ids.length}</TableCell>
                  <TableCell className="text-right tabular-nums">{l.grid_size}</TableCell>
                  <TableCell className="text-muted-foreground">{formatDateTime(l.updated_at)}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon" aria-label={`Delete ${l.name}`} onClick={() => setDeleting(l)}>
                      <Trash2 />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(undefined)}
        title={`Delete "${deleting?.name}"?`}
        description="The layout is removed. The cameras themselves are not affected."
        pending={del.isPending}
        onConfirm={() =>
          deleting &&
          del.mutate(deleting.id, {
            onSuccess: () => {
              toast.success('Layout deleted')
              setDeleting(undefined)
            },
            onError: (e) => {
              toast.error(errorMessage(e))
              setDeleting(undefined)
            },
          })
        }
      />
    </>
  )
}
