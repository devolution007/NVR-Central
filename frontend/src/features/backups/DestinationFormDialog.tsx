import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { ApiError, errorMessage } from '@/api/client'
import type { HostKey, SftpDestination } from '@/api/types'
import { FieldError } from '@/components/FieldError'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { probeHostKey, useCreateDestination, useUpdateDestination } from './api'
import { type DestinationFormValues, makeDestinationSchema } from './destinationSchema'

function DestinationForm({ dest, onDone }: { dest?: SftpDestination; onDone: () => void }) {
  const mode = dest ? 'edit' : 'create'
  const create = useCreateDestination()
  const update = useUpdateDestination()
  const pending = create.isPending || update.isPending

  const {
    register,
    control,
    handleSubmit,
    setError,
    getValues,
    watch,
    formState: { errors },
  } = useForm<DestinationFormValues>({
    resolver: zodResolver(makeDestinationSchema(mode)),
    defaultValues: {
      name: dest?.name ?? '',
      host: dest?.host ?? '',
      port: dest?.port ?? 22,
      username: dest?.username ?? '',
      auth_type: dest?.auth_type ?? 'password',
      password: '',
      private_key: '',
      base_path: dest?.base_path ?? '/backups',
      enabled: dest?.enabled ?? true,
    },
  })
  const authType = watch('auth_type')

  // The host key the administrator has confirmed. Starts as the stored one when editing.
  const [pinned] = useState<HostKey | null>(dest?.host_key_sha256 ? { key_type: dest.host_key_type ?? '', sha256: dest.host_key_sha256 } : null)
  const [fetched, setFetched] = useState<HostKey | null>(null)
  const [fetching, setFetching] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)

  const fetchKey = async () => {
    setFetchError(null)
    setFetched(null)
    setConfirmed(false)
    const { host, port } = getValues()
    if (!host.trim()) return setError('host', { message: 'Enter the address first' })
    setFetching(true)
    try {
      setFetched(await probeHostKey(host.trim(), Number(port) || 22))
    } catch (err) {
      setFetchError(errorMessage(err))
    } finally {
      setFetching(false)
    }
  }

  const trusted = fetched && confirmed ? fetched : pinned
  const changed = fetched !== null && pinned !== null && fetched.sha256 !== pinned.sha256

  const onSubmit = handleSubmit(async (v) => {
    if (!trusted) return setFetchError('Fetch the server\'s host key and confirm its fingerprint first')
    const payload = {
      name: v.name,
      host: v.host,
      port: v.port,
      username: v.username,
      auth_type: v.auth_type,
      base_path: v.base_path,
      enabled: v.enabled,
      host_key_sha256: trusted.sha256,
      host_key_type: trusted.key_type || null,
      ...(v.auth_type === 'password' && v.password ? { password: v.password } : {}),
      ...(v.auth_type === 'key' && v.private_key.trim() ? { private_key: v.private_key.trim() } : {}),
    }
    try {
      if (dest) await update.mutateAsync({ id: dest.id, ...payload })
      else await create.mutateAsync(payload)
      toast.success(dest ? 'Destination updated. Use Test to check it.' : 'Destination saved. Use Test to check it.')
      onDone()
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === 'destination_name_taken') return setError('name', { message: err.message })
        const known = new Set(['name', 'host', 'port', 'username', 'password', 'private_key', 'base_path'])
        const mapped = err.details.filter((d) => known.has(d.field))
        if (mapped.length) {
          mapped.forEach((d) => setError(d.field as keyof DestinationFormValues, { message: d.message }))
          return
        }
      }
      toast.error(errorMessage(err))
    }
  })

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div>
        <Label htmlFor="dest-name">Name</Label>
        <Input id="dest-name" placeholder="Head office archive" aria-invalid={!!errors.name} {...register('name')} />
        <FieldError message={errors.name?.message} />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <Label htmlFor="dest-host">SFTP server address</Label>
          <Input id="dest-host" placeholder="backup.example.com" aria-invalid={!!errors.host} {...register('host')} />
          <FieldError message={errors.host?.message} />
        </div>
        <div>
          <Label htmlFor="dest-port">Port</Label>
          <Input id="dest-port" type="number" inputMode="numeric" aria-invalid={!!errors.port} {...register('port', { valueAsNumber: true })} />
          <FieldError message={errors.port?.message} />
        </div>
      </div>

      <div className="rounded-lg border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm">
            <p className="font-medium">Server identity (host key)</p>
            <p className="text-xs text-muted-foreground">Backups only go to a server presenting exactly this key. This protects against an impostor server.</p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => void fetchKey()} disabled={fetching}>
            {fetching && <Loader2 className="animate-spin" />} Fetch host key
          </Button>
        </div>
        {fetchError && <FieldError message={fetchError} />}
        {fetched && (
          <div className="mt-3 space-y-2 text-sm">
            <p>
              The server presented a <span className="font-mono">{fetched.key_type}</span> key:
            </p>
            <p className="break-all rounded bg-muted p-2 font-mono text-xs" data-testid="fetched-fingerprint">
              {fetched.sha256}
            </p>
            {changed && (
              <p className="flex gap-1 text-warning" role="alert">
                <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> This is different from the key you trusted before. Only continue if the server was reinstalled.
              </p>
            )}
            <div className="flex items-start gap-2">
              <Checkbox id="dest-confirm" checked={confirmed} onCheckedChange={(c) => setConfirmed(c === true)} />
              <Label htmlFor="dest-confirm" className="font-normal leading-snug">
                I compared this fingerprint with the one on the SFTP server (for example with <span className="font-mono">ssh-keygen -lf</span>) and it matches.
              </Label>
            </div>
          </div>
        )}
        {!fetched && pinned && (
          <p className="mt-3 flex items-center gap-1 text-sm text-success">
            <ShieldCheck className="size-4" aria-hidden /> Trusted: <span className="break-all font-mono text-xs">{pinned.sha256}</span>
          </p>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="dest-user">Username</Label>
          <Input id="dest-user" autoComplete="off" aria-invalid={!!errors.username} {...register('username')} />
          <FieldError message={errors.username?.message} />
        </div>
        <div>
          <Label htmlFor="dest-auth">Login method</Label>
          <Controller
            control={control}
            name="auth_type"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="dest-auth" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="password">Password</SelectItem>
                  <SelectItem value="key">Private key</SelectItem>
                </SelectContent>
              </Select>
            )}
          />
        </div>
      </div>

      {authType === 'password' ? (
        <div>
          <Label htmlFor="dest-pass">Password</Label>
          <Input
            id="dest-pass"
            type="password"
            autoComplete="new-password"
            placeholder={dest ? 'Stored. Leave blank to keep' : ''}
            aria-invalid={!!errors.password}
            {...register('password')}
          />
          <FieldError message={errors.password?.message} />
        </div>
      ) : (
        <div>
          <Label htmlFor="dest-key">Private key</Label>
          <Textarea
            id="dest-key"
            rows={5}
            className="font-mono text-xs"
            spellCheck={false}
            autoComplete="off"
            placeholder={dest ? 'Stored. Leave blank to keep' : '-----BEGIN OPENSSH PRIVATE KEY-----'}
            aria-invalid={!!errors.private_key}
            {...register('private_key')}
          />
          <p className="mt-1 text-xs text-muted-foreground">Ed25519, RSA or ECDSA, without a passphrase.</p>
          <FieldError message={errors.private_key?.message} />
        </div>
      )}
      <p className="text-xs text-muted-foreground">Credentials are encrypted on the server and are never shown again.</p>

      <div>
        <Label htmlFor="dest-path">Base folder on the server</Label>
        <Input id="dest-path" className="font-mono" aria-invalid={!!errors.base_path} {...register('base_path')} />
        <p className="mt-1 text-xs text-muted-foreground">Recordings go in subfolders: campus / NVR / camera / stream / year / month / day.</p>
        <FieldError message={errors.base_path?.message} />
      </div>

      <Controller
        control={control}
        name="enabled"
        render={({ field }) => (
          <div className="flex items-center gap-2">
            <Checkbox id="dest-enabled" checked={field.value} onCheckedChange={(c) => field.onChange(c === true)} />
            <Label htmlFor="dest-enabled">Enabled</Label>
          </div>
        )}
      />

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !trusted}>
          {pending ? 'Saving…' : dest ? 'Save changes' : 'Save destination'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function DestinationFormDialog({ open, onOpenChange, dest }: { open: boolean; onOpenChange: (o: boolean) => void; dest?: SftpDestination }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{dest ? 'Edit SFTP destination' : 'Add SFTP destination'}</DialogTitle>
          <DialogDescription>Where backed-up recordings are uploaded. Save, then press Test to prove it works.</DialogDescription>
        </DialogHeader>
        <DestinationForm key={dest?.id ?? 'new'} dest={dest} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}
