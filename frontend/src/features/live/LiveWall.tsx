import { ChevronLeft, ChevronRight, LayoutGrid, Save, Video } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { Camera, GridSize } from '@/api/types'
import { EmptyState, ErrorState } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useCampuses } from '@/features/campuses/api'
import { useCameras, useNvrs } from '@/features/nvrs/api'
import { ApiError } from '@/api/client'
import { useLayouts, useSaveLayout } from './api'
import { CameraFullscreenDialog } from './CameraFullscreenDialog'
import { LiveCameraPlayer } from './LiveCameraPlayer'

export const GRID_SIZES: GridSize[] = [1, 4, 9, 16, 25]
const ALL = 'all'

// Column counts per layout, reduced on narrow screens so tiles stay watchable.
const GRID_CLASS: Record<GridSize, string> = {
  1: 'grid-cols-1',
  4: 'grid-cols-1 sm:grid-cols-2',
  9: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  16: 'grid-cols-2 lg:grid-cols-4',
  25: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5',
}

const parseGrid = (v: string | null): GridSize => (GRID_SIZES.includes(Number(v) as GridSize) ? (Number(v) as GridSize) : 4)

export type WallSource = 'all' | 'favorites'

/** The camera wall: filters, layout size, pagination, saved layouts, fullscreen. State lives in the URL so a view can be
 *  bookmarked and survives a reload. */
export function LiveWall({ source }: { source: WallSource }) {
  const [params, setParams] = useSearchParams()
  const layouts = useLayouts()
  const layoutId = params.get('layout')
  const layout = layouts.data?.find((l) => l.id === layoutId)

  const grid = parseGrid(params.get('grid') ?? (layout ? String(layout.grid_size) : null))
  const campus = params.get('campus') ?? ALL
  const nvr = params.get('nvr') ?? ALL
  const q = params.get('q') ?? ''
  const onlineOnly = params.get('online') !== '0'
  const page = Math.max(1, Number(params.get('page') ?? 1) || 1)

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') next.delete(k)
      else next.set(k, v)
    }
    if (!('page' in patch)) next.delete('page')
    setParams(next, { replace: true })
  }

  const campuses = useCampuses({ page_size: 200 })
  const nvrs = useNvrs({ campus_id: campus === ALL ? undefined : campus, page_size: 200 })
  const cams = useCameras({
    favorites: source === 'favorites' || undefined,
    ids: layout ? layout.camera_ids : undefined,
    campus_id: layout || campus === ALL ? undefined : campus,
    nvr_id: layout || nvr === ALL ? undefined : nvr,
    q: layout ? undefined : q || undefined,
    page_size: 500,
  })

  const all = useMemo(() => {
    let items = cams.data?.items ?? []
    if (layout) {
      const order = new Map(layout.camera_ids.map((id, i) => [id, i]))
      items = [...items].sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0))
    }
    items = items.filter((c) => c.status !== 'unavailable')
    return onlineOnly && !layout ? items.filter((c) => c.status !== 'offline') : items
  }, [cams.data, layout, onlineOnly])

  const pages = Math.max(1, Math.ceil(all.length / grid))
  const current = Math.min(page, pages)
  const visible = all.slice((current - 1) * grid, current * grid)
  const [fullscreen, setFullscreen] = useState<Camera | null>(null)
  const [saving, setSaving] = useState(false)

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-md border p-0.5" role="group" aria-label="Layout size">
          {GRID_SIZES.map((g) => (
            <Button key={g} size="sm" variant={g === grid ? 'default' : 'ghost'} className="h-7 min-w-9 px-2" aria-pressed={g === grid} onClick={() => set({ grid: String(g) })}>
              {g}
            </Button>
          ))}
        </div>

        {source === 'all' && !layout && (
          <>
            <div className="w-44">
              <Select value={campus} onValueChange={(v) => set({ campus: v === ALL ? null : v, nvr: null })}>
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
            <div className="w-44">
              <Select value={nvr} onValueChange={(v) => set({ nvr: v === ALL ? null : v })}>
                <SelectTrigger className="w-full" aria-label="Filter by NVR">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>All NVRs</SelectItem>
                  {nvrs.data?.items.map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Input className="w-48" placeholder="Search cameras…" aria-label="Search cameras" value={q} onChange={(e) => set({ q: e.target.value })} />
            <div className="flex items-center gap-2">
              <Checkbox id="online-only" checked={onlineOnly} onCheckedChange={(c) => set({ online: c === true ? null : '0' })} />
              <Label htmlFor="online-only" className="text-sm font-normal">
                Hide cameras not responding
              </Label>
            </div>
          </>
        )}

        {layout && (
          <div className="flex items-center gap-2 text-sm">
            <LayoutGrid className="size-4" aria-hidden /> Layout: <strong>{layout.name}</strong>
            <Button variant="link" className="h-auto p-0" onClick={() => set({ layout: null, grid: null })}>
              Back to all cameras
            </Button>
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">
                <LayoutGrid /> Saved layouts
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>Open a layout</DropdownMenuLabel>
              {layouts.data?.length ? (
                layouts.data.map((l) => (
                  <DropdownMenuItem key={l.id} onSelect={() => set({ layout: l.id, grid: String(l.grid_size) })}>
                    {l.name} <span className="ml-auto text-xs text-muted-foreground">{l.camera_ids.length}</span>
                  </DropdownMenuItem>
                ))
              ) : (
                <p className="px-2 py-1.5 text-sm text-muted-foreground">None saved yet</p>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem disabled={visible.length === 0} onSelect={() => setSaving(true)}>
                <Save /> Save current view…
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link to="/live/layouts">Manage layouts</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {cams.error ? (
        <ErrorState message={errorMessage(cams.error)} onRetry={() => void cams.refetch()} />
      ) : cams.isLoading ? (
        <div className={`grid gap-3 ${GRID_CLASS[grid]}`}>
          {Array.from({ length: Math.min(grid, 4) }).map((_, i) => (
            <Skeleton key={i} className="aspect-video" />
          ))}
        </div>
      ) : all.length === 0 ? (
        <EmptyState icon={<Video className="size-8" />} title={source === 'favorites' ? 'No favorite cameras yet' : layout ? 'No cameras in this layout' : 'No cameras to show'}>
          {source === 'favorites' ? (
            <>
              Star a camera on the{' '}
              <Link to="/live" className="font-medium text-primary underline underline-offset-4">
                live wall
              </Link>{' '}
              to find it here.
            </>
          ) : (
            <>
              Cameras appear after an NVR is registered and discovered. Check{' '}
              <Link to="/nvrs" className="font-medium text-primary underline underline-offset-4">
                NVR Management
              </Link>
              , or relax the filters.
            </>
          )}
        </EmptyState>
      ) : (
        <>
          <div className={`grid gap-3 ${GRID_CLASS[grid]}`} data-testid="camera-wall">
            {visible.map((c) => (
              <LiveCameraPlayer key={c.id} camera={c} paused={fullscreen !== null} onFullscreen={() => setFullscreen(c)} />
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
            <span>
              {all.length} camera{all.length === 1 ? '' : 's'}. Only tiles on screen stream, and an NVR allows a limited number of streams at once.
            </span>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon" aria-label="Previous page" disabled={current <= 1} onClick={() => set({ page: String(current - 1) })}>
                <ChevronLeft />
              </Button>
              <span aria-live="polite">
                Page {current} of {pages}
              </span>
              <Button variant="outline" size="icon" aria-label="Next page" disabled={current >= pages} onClick={() => set({ page: String(current + 1) })}>
                <ChevronRight />
              </Button>
            </div>
          </div>
        </>
      )}

      <CameraFullscreenDialog camera={fullscreen} onClose={() => setFullscreen(null)} />
      <SaveLayoutDialog open={saving} onClose={() => setSaving(false)} grid={grid} cameras={visible} existingId={layout?.id} existingName={layout?.name} />
    </>
  )
}

function SaveLayoutDialog({ open, onClose, grid, cameras, existingId, existingName }: { open: boolean; onClose: () => void; grid: GridSize; cameras: Camera[]; existingId?: string; existingName?: string }) {
  const [name, setName] = useState(existingName ?? '')
  const save = useSaveLayout()
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save this view</DialogTitle>
          <DialogDescription>
            Saves the {cameras.length} camera{cameras.length === 1 ? '' : 's'} on screen in a {grid}-tile layout. Layouts are personal.
          </DialogDescription>
        </DialogHeader>
        <div>
          <Label htmlFor="layout-name">Layout name</Label>
          <Input id="layout-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Entrances" />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button
            disabled={!name.trim() || save.isPending}
            onClick={() =>
              save.mutate(
                { id: existingName === name.trim() ? existingId : undefined, name: name.trim(), grid_size: grid, camera_ids: cameras.map((c) => c.id) },
                {
                  onSuccess: () => {
                    toast.success('Layout saved')
                    onClose()
                  },
                  onError: (e) => toast.error(e instanceof ApiError && e.code === 'layout_name_taken' ? 'You already have a layout with this name' : errorMessage(e)),
                },
              )
            }
          >
            {save.isPending ? 'Saving…' : 'Save layout'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
