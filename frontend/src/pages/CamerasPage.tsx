import { Camera as CameraIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage } from '@/api/client'
import type { Camera, CameraStatus } from '@/api/types'
import { CameraStatusIndicator } from '@/components/StatusIndicators'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCampuses } from '@/features/campuses/api'
import { useCameras, useNvrs } from '@/features/nvrs/api'
import { streamLabel, timeAgo } from '@/lib/format'

const ALL = 'all'
const STATUS_OPTIONS: { value: CameraStatus; label: string }[] = [
  { value: 'online', label: 'Online' },
  { value: 'offline', label: 'Not responding' },
  { value: 'unknown', label: 'Not checked' },
  { value: 'unavailable', label: 'Removed from NVR' },
]

interface Group {
  key: string
  campus: string
  nvrId: string
  nvr: string
  cameras: Camera[]
}

export function CamerasPage() {
  const [campus, setCampus] = useState(ALL)
  const [nvr, setNvr] = useState(ALL)
  const [status, setStatus] = useState(ALL)
  const [q, setQ] = useState('')

  const campuses = useCampuses({ page_size: 200 })
  const nvrs = useNvrs({ campus_id: campus === ALL ? undefined : campus, page_size: 200 })
  const cams = useCameras({
    campus_id: campus === ALL ? undefined : campus,
    nvr_id: nvr === ALL ? undefined : nvr,
    status: status === ALL ? undefined : (status as CameraStatus),
    q: q || undefined,
    page_size: 500,
  })

  const groups = useMemo<Group[]>(() => {
    const map = new Map<string, Group>()
    for (const c of cams.data?.items ?? []) {
      const key = `${c.campus_id}/${c.nvr_id}`
      if (!map.has(key)) map.set(key, { key, campus: c.campus_name, nvrId: c.nvr_id, nvr: c.nvr_name, cameras: [] })
      map.get(key)!.cameras.push(c)
    }
    return [...map.values()]
  }, [cams.data])

  const filtered = campus !== ALL || nvr !== ALL || status !== ALL || q !== ''

  return (
    <>
      <PageHeader title="Camera Management" description="Cameras discovered on each NVR, grouped by campus" />

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="w-full sm:w-52">
          <Select
            value={campus}
            onValueChange={(v) => {
              setCampus(v)
              setNvr(ALL)
            }}
          >
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
        <div className="w-full sm:w-52">
          <Select value={nvr} onValueChange={setNvr}>
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
        <div className="w-full sm:w-44">
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-full" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Any status</SelectItem>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="w-full sm:w-64">
          <Input placeholder="Search cameras…" aria-label="Search cameras" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {cams.error ? (
        <ErrorState message={errorMessage(cams.error)} onRetry={() => void cams.refetch()} />
      ) : cams.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        <EmptyState icon={<CameraIcon className="size-8" />} title={filtered ? 'No cameras match these filters' : 'No cameras discovered yet'}>
          {filtered ? (
            'Try clearing a filter.'
          ) : (
            <>
              Cameras appear after an NVR connects and its channels are discovered. Register or test NVRs on the{' '}
              <Link to="/nvrs" className="font-medium text-primary underline underline-offset-4">
                NVR Management
              </Link>{' '}
              page.
            </>
          )}
        </EmptyState>
      ) : (
        <div className="space-y-6">
          <p className="text-sm text-muted-foreground">
            {cams.data?.total} camera{cams.data?.total === 1 ? '' : 's'}. Backup settings, including main or substream per camera (substream by default), are added in Phase 4.
          </p>
          {groups.map((g) => (
            <section key={g.key}>
              <h2 className="mb-2 text-sm font-medium">
                {g.campus} <span className="text-muted-foreground">/</span>{' '}
                <Link to={`/nvrs/${g.nvrId}`} className="hover:underline">
                  {g.nvr}
                </Link>
              </h2>
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-16">Ch.</TableHead>
                      <TableHead>Name</TableHead>
                      <TableHead>Camera model</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Main stream</TableHead>
                      <TableHead>Substream</TableHead>
                      <TableHead>Last seen in discovery</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {g.cameras.map((c) => (
                      <TableRow key={c.id} className={c.status === 'unavailable' ? 'opacity-60' : undefined}>
                        <TableCell className="tabular-nums">{c.channel_number}</TableCell>
                        <TableCell className="font-medium">{c.name}</TableCell>
                        <TableCell className="text-muted-foreground">{c.model ?? '—'}</TableCell>
                        <TableCell>
                          <CameraStatusIndicator status={c.status} detail={c.status_detail} />
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{streamLabel(c.main_stream)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{streamLabel(c.sub_stream)}</TableCell>
                        <TableCell className="text-muted-foreground">{timeAgo(c.last_discovered_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          ))}
        </div>
      )}
    </>
  )
}
