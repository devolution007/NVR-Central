import { Building2, Camera, DatabaseBackup, Server } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { NvrStatus } from '@/api/types'
import { errorMessage } from '@/api/client'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { NvrStatusIndicator } from '@/components/NvrStatusIndicator'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { isAdmin, useAuth } from '@/features/auth/store'
import { useDashboardSummary } from '@/features/nvrs/api'

function StatCard({ label, value, icon, hint }: { label: string; value: number; icon: React.ReactNode; hint?: string }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        <span className="text-muted-foreground" aria-hidden>
          {icon}
        </span>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">{value}</div>
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  )
}

const STATUS_ORDER: NvrStatus[] = ['connected', 'pending', 'connecting', 'offline', 'auth_failed', 'p2p_unavailable', 'partially_supported', 'unsupported']

export function DashboardPage() {
  const user = useAuth((s) => s.user)
  const { data, isLoading, error, refetch } = useDashboardSummary()

  return (
    <>
      <PageHeader title="Dashboard" description="Overview of your campuses, NVRs and cameras" />
      {error ? (
        <ErrorState message={errorMessage(error)} onRetry={() => void refetch()} />
      ) : isLoading || !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      ) : data.campuses === 0 ? (
        <EmptyState icon={<Building2 className="size-8" />} title="No campuses yet">
          {isAdmin(user) ? (
            <>
              Start by <Link to="/campuses" className="font-medium text-primary underline underline-offset-4">adding a campus</Link>, then register its NVRs.
            </>
          ) : (
            'An administrator needs to give you access to a campus.'
          )}
        </EmptyState>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Campuses" value={data.campuses} icon={<Building2 className="size-4" />} />
            <StatCard label="NVRs" value={data.nvrs_total} icon={<Server className="size-4" />} hint={`${data.nvrs_by_status.connected} connected`} />
            <StatCard label="Cameras" value={data.cameras} icon={<Camera className="size-4" />} hint={data.cameras === 0 ? 'Discovered after NVR connection (Phase 2)' : undefined} />
            <StatCard label="Cameras with backup" value={data.cameras_with_backup} icon={<DatabaseBackup className="size-4" />} />
          </div>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">NVR connection status</CardTitle>
            </CardHeader>
            <CardContent>
              {data.nvrs_total === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No NVRs registered. <Link to="/nvrs" className="font-medium text-primary underline underline-offset-4">Register one</Link>.
                </p>
              ) : (
                <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {STATUS_ORDER.filter((s) => data.nvrs_by_status[s] > 0).map((s) => (
                    <li key={s} className="flex items-center justify-between rounded-md border px-3 py-2">
                      <NvrStatusIndicator status={s} />
                      <span className="tabular-nums text-lg font-semibold">{data.nvrs_by_status[s]}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-4 text-xs text-muted-foreground">
                "Not tested" means no connection test has run. It is not the same as offline.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}
