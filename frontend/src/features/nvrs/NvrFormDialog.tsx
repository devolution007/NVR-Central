import { zodResolver } from '@hookform/resolvers/zod'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { ApiError, errorMessage } from '@/api/client'
import type { Campus, Nvr, NvrInput } from '@/api/types'
import { FieldError } from '@/components/FieldError'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useCreateNvr, useUpdateNvr } from './api'
import { makeNvrSchema, type NvrFormValues } from './schema'

const CONNECTION_LABEL = {
  tunnel: 'Campus tunnel (recommended)',
  lan: 'Direct LAN / existing VPN address',
  p2p: 'Dahua P2P (experimental)',
} as const

function NvrForm({ nvr, campuses, defaultCampusId, onDone }: { nvr?: Nvr; campuses: Campus[]; defaultCampusId?: string; onDone: () => void }) {
  const mode = nvr ? 'edit' : 'create'
  const create = useCreateNvr()
  const update = useUpdateNvr()
  const pending = create.isPending || update.isPending

  const {
    register,
    control,
    handleSubmit,
    setError,
    watch,
    formState: { errors },
  } = useForm<NvrFormValues>({
    resolver: zodResolver(makeNvrSchema(mode)),
    defaultValues: {
      campus_id: nvr?.campus_id ?? defaultCampusId ?? '',
      name: nvr?.name ?? '',
      connection_type: nvr?.connection_type ?? 'tunnel',
      host: nvr?.host ?? '',
      http_port: nvr?.http_port ?? 80,
      rtsp_port: nvr?.rtsp_port ?? 554,
      username: nvr?.username ?? '',
      password: '',
      p2p_serial: '',
      enabled: nvr?.enabled ?? true,
    },
  })
  const type = watch('connection_type')

  const onSubmit = handleSubmit(async (v) => {
    const payload: NvrInput = {
      campus_id: v.campus_id,
      name: v.name,
      connection_type: v.connection_type,
      host: v.host || null,
      http_port: v.http_port,
      rtsp_port: v.rtsp_port,
      username: v.username,
      enabled: v.enabled,
      ...(v.password ? { password: v.password } : {}),
      ...(v.connection_type === 'p2p' && v.p2p_serial ? { p2p_serial: v.p2p_serial } : {}),
    }
    try {
      if (nvr) await update.mutateAsync({ id: nvr.id, ...payload })
      else await create.mutateAsync(payload)
      toast.success(nvr ? 'NVR updated' : 'NVR registered. It stays "Not tested" until connection testing arrives in Phase 2.')
      onDone()
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === 'nvr_name_taken') return setError('name', { message: err.message })
        const known = new Set(['campus_id', 'name', 'host', 'http_port', 'rtsp_port', 'username', 'password', 'p2p_serial'])
        const mapped = err.details.filter((d) => known.has(d.field))
        if (mapped.length) {
          mapped.forEach((d) => setError(d.field as keyof NvrFormValues, { message: d.message }))
          return
        }
      }
      toast.error(errorMessage(err))
    }
  })

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="nvr-campus">Campus</Label>
          <Controller
            control={control}
            name="campus_id"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="nvr-campus" className="w-full" aria-invalid={!!errors.campus_id}>
                  <SelectValue placeholder="Choose a campus" />
                </SelectTrigger>
                <SelectContent>
                  {campuses.map((c) => (
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
          <Label htmlFor="nvr-name">Display name</Label>
          <Input id="nvr-name" aria-invalid={!!errors.name} {...register('name')} />
          <FieldError message={errors.name?.message} />
        </div>
      </div>

      <div>
        <Label htmlFor="nvr-type">How the server reaches this NVR</Label>
        <Controller
          control={control}
          name="connection_type"
          render={({ field }) => (
            <Select value={field.value} onValueChange={field.onChange}>
              <SelectTrigger id="nvr-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(CONNECTION_LABEL) as (keyof typeof CONNECTION_LABEL)[]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {CONNECTION_LABEL[k]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        />
        {type === 'p2p' && (
          <p className="mt-1 text-xs text-warning">
            P2P failed hardware testing on the test NVR. Use it only for NVRs where it has been verified.
          </p>
        )}
      </div>

      {type !== 'p2p' ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="sm:col-span-1">
            <Label htmlFor="nvr-host">{type === 'tunnel' ? 'Tunnel address' : 'IP or hostname'}</Label>
            <Input id="nvr-host" placeholder="192.168.15.6" aria-invalid={!!errors.host} {...register('host')} />
            <FieldError message={errors.host?.message} />
          </div>
          <div>
            <Label htmlFor="nvr-http">HTTP port</Label>
            <Input id="nvr-http" type="number" inputMode="numeric" aria-invalid={!!errors.http_port} {...register('http_port', { valueAsNumber: true })} />
            <FieldError message={errors.http_port?.message} />
          </div>
          <div>
            <Label htmlFor="nvr-rtsp">RTSP port</Label>
            <Input id="nvr-rtsp" type="number" inputMode="numeric" aria-invalid={!!errors.rtsp_port} {...register('rtsp_port', { valueAsNumber: true })} />
            <FieldError message={errors.rtsp_port?.message} />
          </div>
        </div>
      ) : (
        <div>
          <Label htmlFor="nvr-serial">P2P serial number</Label>
          <Input
            id="nvr-serial"
            autoComplete="off"
            placeholder={nvr?.p2p_serial_masked ? `Stored (${nvr.p2p_serial_masked}). Leave blank to keep` : ''}
            aria-invalid={!!errors.p2p_serial}
            {...register('p2p_serial')}
          />
          <FieldError message={errors.p2p_serial?.message} />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="nvr-user">NVR username</Label>
          <Input id="nvr-user" autoComplete="off" aria-invalid={!!errors.username} {...register('username')} />
          <FieldError message={errors.username?.message} />
        </div>
        <div>
          <Label htmlFor="nvr-pass">NVR password</Label>
          <Input
            id="nvr-pass"
            type="password"
            autoComplete="new-password"
            placeholder={nvr ? 'Stored. Leave blank to keep' : ''}
            aria-invalid={!!errors.password}
            {...register('password')}
          />
          <FieldError message={errors.password?.message} />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Credentials are encrypted on the server and are never shown again or sent back to the browser.
      </p>

      <Controller
        control={control}
        name="enabled"
        render={({ field }) => (
          <div className="flex items-center gap-2">
            <Checkbox id="nvr-enabled" checked={field.value} onCheckedChange={(c) => field.onChange(c === true)} />
            <Label htmlFor="nvr-enabled">Enabled</Label>
          </div>
        )}
      />

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : nvr ? 'Save changes' : 'Register NVR'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function NvrFormDialog({
  open,
  onOpenChange,
  nvr,
  campuses,
  defaultCampusId,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  nvr?: Nvr
  campuses: Campus[]
  defaultCampusId?: string
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{nvr ? 'Edit NVR' : 'Register NVR'}</DialogTitle>
          <DialogDescription>
            Saving does not contact the NVR yet. Connection testing and camera discovery arrive in Phase 2.
          </DialogDescription>
        </DialogHeader>
        <NvrForm key={nvr?.id ?? 'new'} nvr={nvr} campuses={campuses} defaultCampusId={defaultCampusId} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}
