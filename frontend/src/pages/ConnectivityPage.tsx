import { zodResolver } from '@hookform/resolvers/zod'
import { Copy, Download, Plus, Trash2, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { ApiError, errorMessage } from '@/api/client'
import type { PeerCreated, TunnelPeer } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { FieldError } from '@/components/FieldError'
import { EmptyState, ErrorState, PageHeader } from '@/components/PageHeader'
import { PeerStatusIndicator } from '@/components/StatusIndicators'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useCampuses } from '@/features/campuses/api'
import { fetchHubConfig, useCreatePeer, useHub, usePeers, useRevokePeer } from '@/features/tunnel/api'
import { formatBytes, timeAgo } from '@/lib/format'

const LAN_RE = /^(\d{1,3}\.){3}0\/24$/
const IPV4_RE = /^(\d{1,3}\.){3}\d{1,3}$/

const port = (label: string) =>
  z.number({ error: `${label} must be a number` }).int().min(1, `${label} must be between 1 and 65535`).max(65535, `${label} must be between 1 and 65535`)

export const peerSchema = z
  .object({
    campus_id: z.string().min(1, 'Choose a campus'),
    name: z.string().trim().min(1, 'Name is required').max(128),
    mode: z.enum(['windows', 'connector', 'router']),
    lan_cidr: z.string().trim(),
    public_key: z.string().trim().max(64),
    nvr_host: z.string().trim(),
    nvr_http_port: port('HTTP port'),
    nvr_rtsp_port: port('RTSP port'),
  })
  .superRefine((v, ctx) => {
    const lanMessage = 'Use the campus LAN as a /24 network, for example 192.168.15.0/24'
    if (v.mode === 'windows') {
      if (!IPV4_RE.test(v.nvr_host)) ctx.addIssue({ code: 'custom', path: ['nvr_host'], message: "Enter the NVR's real LAN address, for example 192.168.15.6" })
      if (v.lan_cidr && !LAN_RE.test(v.lan_cidr)) ctx.addIssue({ code: 'custom', path: ['lan_cidr'], message: lanMessage })
    } else if (!LAN_RE.test(v.lan_cidr)) {
      ctx.addIssue({ code: 'custom', path: ['lan_cidr'], message: lanMessage })
    }
  })
type PeerForm = z.infer<typeof peerSchema>

function download(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('Copied')
  } catch {
    toast.error('Could not copy. Select the text and copy it manually.')
  }
}

function PeerDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (c: PeerCreated) => void }) {
  const campuses = useCampuses({ page_size: 200 })
  const create = useCreatePeer()
  const {
    register,
    control,
    handleSubmit,
    setError,
    watch,
    reset,
    formState: { errors },
  } = useForm<PeerForm>({
    resolver: zodResolver(peerSchema),
    defaultValues: { campus_id: '', name: '', mode: 'windows', lan_cidr: '', public_key: '', nvr_host: '', nvr_http_port: 80, nvr_rtsp_port: 554 },
  })
  const mode = watch('mode')

  const submit = handleSubmit(async (v) => {
    try {
      const created = await create.mutateAsync({
        campus_id: v.campus_id,
        name: v.name,
        mode: v.mode,
        lan_cidr: v.lan_cidr || undefined,
        public_key: v.public_key || undefined,
        ...(v.mode === 'windows' ? { nvr_host: v.nvr_host, nvr_http_port: v.nvr_http_port, nvr_rtsp_port: v.nvr_rtsp_port } : {}),
      })
      reset()
      onOpenChange(false)
      onCreated(created)
    } catch (err) {
      if (err instanceof ApiError && ['invalid_lan', 'lan_overlap'].includes(err.code)) setError('lan_cidr', { message: err.message })
      else if (err instanceof ApiError && err.code === 'invalid_nvr_host') setError('nvr_host', { message: err.message })
      else if (err instanceof ApiError && ['invalid_public_key', 'duplicate_key'].includes(err.code)) setError('public_key', { message: err.message })
      else toast.error(errorMessage(err))
    }
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add campus tunnel</DialogTitle>
          <DialogDescription>Creates the campus end of a WireGuard tunnel to this server and its configuration.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="peer-campus">Campus</Label>
              <Controller
                control={control}
                name="campus_id"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="peer-campus" className="w-full">
                      <SelectValue placeholder="Choose a campus" />
                    </SelectTrigger>
                    <SelectContent>
                      {campuses.data?.items.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
              <FieldError message={errors.campus_id?.message} />
            </div>
            <div>
              <Label htmlFor="peer-name">Name</Label>
              <Input id="peer-name" placeholder="Head office connector" {...register('name')} />
              <FieldError message={errors.name?.message} />
            </div>
          </div>
          <div>
            <Label htmlFor="peer-mode">What holds the tunnel at the campus</Label>
            <Controller
              control={control}
              name="mode"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="peer-mode" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="windows">Windows PC (WireGuard for Windows, forwards the NVR's ports)</SelectItem>
                    <SelectItem value="connector">Linux connector (handles overlapping LAN ranges)</SelectItem>
                    <SelectItem value="router">Campus router with its own WireGuard client</SelectItem>
                  </SelectContent>
                </Select>
              )}
            />
          </div>
          {mode === 'windows' && (
            <div className="space-y-3 rounded-md border p-3">
              <div>
                <Label htmlFor="peer-nvr">NVR's real LAN address</Label>
                <Input id="peer-nvr" placeholder="192.168.15.6" aria-invalid={!!errors.nvr_host} {...register('nvr_host')} />
                <p className="mt-1 text-xs text-muted-foreground">
                  The Windows PC forwards the NVR's ports from its tunnel address. You will register the NVR in the app at that tunnel address.
                </p>
                <FieldError message={errors.nvr_host?.message} />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="peer-http">NVR HTTP port</Label>
                  <Input id="peer-http" type="number" inputMode="numeric" {...register('nvr_http_port', { valueAsNumber: true })} />
                  <FieldError message={errors.nvr_http_port?.message} />
                </div>
                <div>
                  <Label htmlFor="peer-rtsp">NVR RTSP port</Label>
                  <Input id="peer-rtsp" type="number" inputMode="numeric" {...register('nvr_rtsp_port', { valueAsNumber: true })} />
                  <FieldError message={errors.nvr_rtsp_port?.message} />
                </div>
              </div>
            </div>
          )}
          <div>
            <Label htmlFor="peer-lan">Campus LAN (as it really is){mode === 'windows' ? ' (optional)' : ''}</Label>
            <Input id="peer-lan" placeholder="192.168.15.0/24" aria-invalid={!!errors.lan_cidr} {...register('lan_cidr')} />
            <FieldError message={errors.lan_cidr?.message} />
          </div>
          <div>
            <Label htmlFor="peer-key">Public key {mode === 'router' ? '(recommended)' : '(optional)'}</Label>
            <Input id="peer-key" autoComplete="off" placeholder="Leave blank to generate a keypair here" {...register('public_key')} />
            <p className="mt-1 text-xs text-muted-foreground">
              If the router or connector makes its own key, paste its public key. Then no private key ever passes through this server.
            </p>
            <FieldError message={errors.public_key?.message} />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? 'Creating…' : 'Create tunnel'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function CreatedDialog({ created, onClose }: { created: PeerCreated | null; onClose: () => void }) {
  return (
    <Dialog open={created !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Tunnel created: {created?.peer.name}</DialogTitle>
          <DialogDescription>
            Address NVRs at this campus as <strong>{created?.peer.nvr_address_pattern}</strong>.
          </DialogDescription>
        </DialogHeader>
        {created?.warnings.map((w) => (
          <p key={w} role="alert" className="flex gap-2 rounded-md border border-warning/50 bg-warning/10 p-2 text-sm">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden /> {w}
          </p>
        ))}
        {created && (
          <>
            <p role="alert" className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-sm">
              {created.config_kind === 'wg_quick' ? (
                <>
                  <strong>This configuration contains the campus private key and is shown only once.</strong> It is not stored on the server.
                </>
              ) : (
                <>
                  <strong>These settings include the pre-shared key and are shown only once.</strong> The router keeps its own private key, which this server never sees.
                  Without this pre-shared key the tunnel cannot connect.
                </>
              )}{' '}
              Copy or download it now and keep it private.
            </p>
            <h3 className="text-sm font-medium">1. WireGuard {created.config_kind === 'wg_quick' ? 'configuration' : 'settings'}</h3>
            <pre className="max-h-72 overflow-auto rounded-md border bg-muted p-3 text-xs" aria-label="WireGuard configuration">
              {created.config}
            </pre>
            {created.script && (
              <>
                <h3 className="text-sm font-medium">2. Forwarding script: run on the Windows PC in PowerShell as Administrator, after the tunnel is active</h3>
                <pre className="max-h-64 overflow-auto rounded-md border bg-muted p-3 text-xs" aria-label="PowerShell forwarding script">
                  {created.script}
                </pre>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => void copy(created.script ?? '')}>
                    <Copy /> Copy script
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => download(created.script_name ?? 'forward.ps1', created.script ?? '')}>
                    <Download /> Download .ps1
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Then register the NVR in the app at the tunnel address <code>{created.peer.tunnel_ip}</code> (not its own address).
                </p>
              </>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => void copy(created.config)}>
                <Copy /> Copy
              </Button>
              <Button
                variant="outline"
                onClick={() => download(`${created.peer.name.replace(/\W+/g, '-')}.${created.config_kind === 'wg_quick' ? 'conf' : 'txt'}`, created.config)}
              >
                <Download /> Download {created.config_kind === 'wg_quick' ? '.conf' : '.txt'}
              </Button>
              <Button onClick={onClose}>I have saved it</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function ConnectivityPage() {
  const hub = useHub()
  const peers = usePeers()
  const revoke = useRevokePeer()
  const [adding, setAdding] = useState(false)
  const [created, setCreated] = useState<PeerCreated | null>(null)
  const [revoking, setRevoking] = useState<TunnelPeer | undefined>()

  return (
    <>
      <PageHeader
        title="Campus Connectivity"
        description="Private WireGuard tunnels from each campus to this server. NVRs never need to be exposed to the internet."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => fetchHubConfig().then((t) => download('wg0.conf', t)).catch((e: unknown) => toast.error(errorMessage(e)))}
              disabled={!hub.data?.public_key}
            >
              <Download /> Hub config
            </Button>
            <Button onClick={() => setAdding(true)}>
              <Plus /> Add campus tunnel
            </Button>
          </>
        }
      />

      <div role="note" className="mb-4 rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm">
        <strong>Not verified on hardware yet.</strong> Keys and configuration files are generated and unit tested, but no tunnel has been brought up against a real hub
        or campus device. See docs/tunnel.md for the steps and what remains to be proven.
      </div>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Hub</CardTitle>
        </CardHeader>
        <CardContent>
          {hub.error ? (
            <ErrorState message={errorMessage(hub.error)} />
          ) : !hub.data ? (
            <Skeleton className="h-16" />
          ) : (
            <dl className="grid gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Public endpoint</dt>
                <dd className="font-mono text-sm">{hub.data.endpoint ?? 'Not set (WG_HUB_ENDPOINT)'}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Tunnel network</dt>
                <dd className="font-mono text-sm">
                  {hub.data.network} (hub {hub.data.hub_address})
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Hub public key</dt>
                <dd className="break-all font-mono text-sm">{hub.data.public_key ?? 'Not configured'}</dd>
              </div>
              {hub.data.notes.map((n) => (
                <p key={n} className="text-sm text-muted-foreground sm:col-span-2">
                  {n}
                </p>
              ))}
            </dl>
          )}
        </CardContent>
      </Card>

      {peers.error ? (
        <ErrorState message={errorMessage(peers.error)} onRetry={() => void peers.refetch()} />
      ) : !peers.data ? (
        <Skeleton className="h-32" />
      ) : peers.data.length === 0 ? (
        <EmptyState title="No campus tunnels yet">Add one for each campus whose NVRs are not reachable directly from this server.</EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campus</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Tunnel address</TableHead>
                <TableHead>Address NVRs as</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Traffic</TableHead>
                <TableHead className="w-16 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {peers.data.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="font-medium">{p.campus_name}</TableCell>
                  <TableCell>{p.name}</TableCell>
                  <TableCell className="capitalize text-muted-foreground">{p.mode}</TableCell>
                  <TableCell className="font-mono text-xs">{p.tunnel_ip}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{p.nvr_address_pattern}</TableCell>
                  <TableCell>
                    <PeerStatusIndicator status={p.status} />
                    <div className="text-xs text-muted-foreground">last handshake {timeAgo(p.last_handshake_at)}</div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    ↓ {formatBytes(p.rx_bytes)} ↑ {formatBytes(p.tx_bytes)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="icon" aria-label={`Revoke ${p.name}`} onClick={() => setRevoking(p)}>
                      <Trash2 />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <PeerDialog open={adding} onOpenChange={setAdding} onCreated={setCreated} />
      <CreatedDialog created={created} onClose={() => setCreated(null)} />
      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(undefined)}
        title={`Revoke "${revoking?.name}"?`}
        description="The campus loses its tunnel once the hub reloads its configuration. NVRs addressed through it become unreachable. The tunnel address is freed for reuse."
        confirmLabel="Revoke"
        pending={revoke.isPending}
        onConfirm={() =>
          revoking &&
          revoke.mutate(revoking.id, {
            onSuccess: () => {
              toast.success('Tunnel revoked. Reload the hub to apply it.')
              setRevoking(undefined)
            },
            onError: (e) => {
              toast.error(errorMessage(e))
              setRevoking(undefined)
            },
          })
        }
      />
    </>
  )
}
