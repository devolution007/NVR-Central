import { HardDrive, Loader2, Pencil, Plus, PlugZap, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { SftpDestination } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { isAdmin, useAuth } from '@/features/auth/store'
import { useDeleteDestination, useDestinations, useTestDestination } from '@/features/backups/api'
import { DestinationFormDialog } from '@/features/backups/DestinationFormDialog'
import { timeAgo } from '@/lib/format'
import { cn } from '@/lib/utils'

function TestResult({ d }: { d: SftpDestination }) {
  if (d.last_test_ok === null)
    return (
      <span className="text-muted-foreground" title="No test has been run since the last change. That is not the same as failing.">
        Not tested
      </span>
    )
  return (
    <span className={cn('block max-w-xs', d.last_test_ok ? 'text-success' : 'text-destructive')} title={d.last_test_message ?? undefined}>
      {d.last_test_ok ? 'Working' : 'Failed'} <span className="text-muted-foreground">· {timeAgo(d.last_test_at)}</span>
      {!d.last_test_ok && d.last_test_message && <span className="block truncate text-xs">{d.last_test_message}</span>}
    </span>
  )
}

export function SftpPage() {
  const admin = isAdmin(useAuth((s) => s.user))
  const { data, isLoading, error, refetch } = useDestinations(admin)
  const test = useTestDestination()
  const del = useDeleteDestination()
  const [editing, setEditing] = useState<SftpDestination | undefined>()
  const [formOpen, setFormOpen] = useState(false)
  const [deleting, setDeleting] = useState<SftpDestination | undefined>()
  const [testingId, setTestingId] = useState<string | null>(null)

  if (!admin)
    return (
      <>
        <PageHeader title="SFTP Destinations" />
        <EmptyState icon={<HardDrive className="size-8" />} title="Administrators only">
          SFTP destinations hold server credentials, so only administrators can see and change them.
        </EmptyState>
      </>
    )

  const openForm = (d?: SftpDestination) => {
    setEditing(d)
    setFormOpen(true)
  }

  const runTest = (d: SftpDestination) => {
    setTestingId(d.id)
    test.mutate(d.id, {
      onSuccess: (r) => (r.last_test_ok ? toast.success(`${r.name}: connected and wrote a test file`) : toast.error(`${r.name}: ${r.last_test_message}`)),
      onError: (err) => toast.error(errorMessage(err)),
      onSettled: () => setTestingId(null),
    })
  }

  return (
    <>
      <PageHeader
        title="SFTP Destinations"
        description="The servers that receive backed-up recordings"
        actions={
          <Button onClick={() => openForm()}>
            <Plus /> Add destination
          </Button>
        }
      />

      {error ? (
        <ErrorState message={errorMessage(error)} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : data && data.length === 0 ? (
        <EmptyState icon={<HardDrive className="size-8" />} title="No SFTP destinations yet">
          Add the SFTP server that should receive backups. You confirm the server&apos;s identity once, and every later connection must match it.
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Server</TableHead>
                <TableHead>Base folder</TableHead>
                <TableHead>Last test</TableHead>
                <TableHead className="text-right">Cameras using it</TableHead>
                <TableHead className="w-48 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data?.map((d) => (
                <TableRow key={d.id} className={d.enabled ? undefined : 'opacity-60'}>
                  <TableCell className="font-medium">
                    {d.name}
                    {!d.enabled && <span className="ml-2 text-xs font-normal text-muted-foreground">(disabled)</span>}
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-xs">
                      {d.username}@{d.host}:{d.port}
                    </span>
                    <span className="block text-xs text-muted-foreground">{d.auth_type === 'key' ? 'Key login' : 'Password login'}</span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{d.base_path}</TableCell>
                  <TableCell>
                    <TestResult d={d} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{d.policy_count}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <Button variant="outline" size="sm" onClick={() => runTest(d)} disabled={testingId === d.id} aria-label={`Test ${d.name}`}>
                        {testingId === d.id ? <Loader2 className="animate-spin" /> : <PlugZap />} Test
                      </Button>
                      <Button variant="ghost" size="icon" aria-label={`Edit ${d.name}`} onClick={() => openForm(d)}>
                        <Pencil />
                      </Button>
                      <Button variant="ghost" size="icon" aria-label={`Delete ${d.name}`} onClick={() => setDeleting(d)}>
                        <Trash2 />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <DestinationFormDialog open={formOpen} onOpenChange={setFormOpen} dest={editing} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(undefined)}
        title={`Delete "${deleting?.name}"?`}
        description="Backups already uploaded stay on the server. A destination that cameras still use cannot be deleted."
        pending={del.isPending}
        onConfirm={() =>
          deleting &&
          del.mutate(deleting.id, {
            onSuccess: () => {
              toast.success('Destination deleted')
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
