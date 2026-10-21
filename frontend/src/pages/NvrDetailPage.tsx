import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, errorMessage } from '@/api/client'
import { CameraStatusIndicator, CapabilityIndicator } from '@/components/StatusIndicators'
import { NvrStatusIndicator } from '@/components/NvrStatusIndicator'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { isAdmin, useAuth } from '@/features/auth/store'
import { useNvr, useNvrCameras, useNvrCapabilities } from '@/features/nvrs/api'
import { NvrActions } from '@/features/nvrs/NvrActions'
import { formatDateTime, streamLabel, timeAgo, utcOffsetLabel } from '@/lib/format'

const CAPABILITY_LABEL: Record<string, string> = {
  p2p_auth: 'P2P login',
  device_info: 'Device information',
  channel_discovery: 'Camera discovery',
  live_video: 'Live video',
  recording_search: 'Recording search',
  recording_download: 'Recording download',
  snapshot: 'Snapshots',
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  )
}

export function NvrDetailPage() {
  const { id = '' } = useParams()
  const admin = isAdmin(useAuth((s) => s.user))
  const nvr = useNvr(id)
  const status = nvr.data?.status
  const caps = useNvrCapabilities(id, status)
  const cams = useNvrCameras(id, status)

  if (nvr.error) {
    const missing = nvr.error instanceof ApiError && nvr.error.status === 404
    return (
      <>
        <BackLink />
        {missing ? <EmptyState title="NVR not found">It may have been deleted, or you may not have access to its campus.</EmptyState> : <ErrorState message={errorMessage(nvr.error)} onRetry={() => void nvr.refetch()} />}
      </>
    )
  }
  if (!nvr.data) return <Skeleton className="h-64" />
  const n = nvr.data

  return (
    <>
      <BackLink />
      <PageHeader title={n.name} description={`${n.campus_name} · ${n.connection_type === 'p2p' ? 'P2P' : `${n.host}:${n.http_port}`}`} actions={admin && <NvrActions nvr={n} labelled />} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-base">Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <NvrStatusIndicator status={n.status} className="text-base" />
            {n.status_message && <p className="rounded-md bg-muted p-2 text-sm text-muted-foreground">{n.status_message}</p>}
            <dl className="grid grid-cols-2 gap-3">
              <Fact label="Model">{n.model ?? '—'}</Fact>
              <Fact label="Firmware">{n.firmware ?? '—'}</Fact>
              <Fact label="NVR timezone">{utcOffsetLabel(n.utc_offset_minutes)}</Fact>
              <Fact label="Cameras">{n.camera_count}</Fact>
              <Fact label="Last checked">
                <span title={formatDateTime(n.last_checked_at)}>{timeAgo(n.last_checked_at)}</span>
              </Fact>
              <Fact label="Last success">
                <span title={formatDateTime(n.last_success_at)}>{timeAgo(n.last_success_at)}</span>
              </Fact>
            </dl>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">What this NVR can do</CardTitle>
            <p className="text-sm text-muted-foreground">Only things observed working on this NVR show as Verified. Anything else is not claimed.</p>
          </CardHeader>
          <CardContent>
            {caps.error ? (
              <ErrorState message={errorMessage(caps.error)} />
            ) : (
              <Table>
                <TableBody>
                  {caps.data?.map((c) => (
                    <TableRow key={c.name}>
                      <TableCell className="w-44 font-medium">{CAPABILITY_LABEL[c.name] ?? c.name}</TableCell>
                      <TableCell className="w-40">
                        <CapabilityIndicator status={c.status} />
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">{c.detail}</TableCell>
                    </TableRow>
                  )) ?? (
                    <TableRow>
                      <TableCell>
                        <Skeleton className="h-6" />
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <h2 className="mb-3 mt-8 text-lg font-semibold">Cameras</h2>
      {cams.error ? (
        <ErrorState message={errorMessage(cams.error)} />
      ) : cams.data && cams.data.length === 0 ? (
        <EmptyState title="No cameras discovered yet">
          {n.status === 'pending' || n.status === 'offline' ? 'Run a connection test or discovery once the NVR is reachable.' : 'Use "Discover cameras" to read the camera list from the NVR.'}
        </EmptyState>
      ) : (
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
              </TableRow>
            </TableHeader>
            <TableBody>
              {cams.data?.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="tabular-nums">{c.channel_number}</TableCell>
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell className="text-muted-foreground">{c.model ?? '—'}</TableCell>
                  <TableCell>
                    <CameraStatusIndicator status={c.status} detail={c.status_detail} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{streamLabel(c.main_stream)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{streamLabel(c.sub_stream)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  )
}

function BackLink() {
  return (
    <Link to="/nvrs" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" /> NVR Management
    </Link>
  )
}
