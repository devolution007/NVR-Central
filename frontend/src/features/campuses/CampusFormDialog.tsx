import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import type { Campus } from '@/api/types'
import { ApiError, errorMessage } from '@/api/client'
import { FieldError } from '@/components/FieldError'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { campusSchema, type CampusFormValues } from '@/features/nvrs/schema'
import { useCreateCampus, useUpdateCampus } from './api'

function CampusForm({ campus, onDone }: { campus?: Campus; onDone: () => void }) {
  const create = useCreateCampus()
  const update = useUpdateCampus()
  const pending = create.isPending || update.isPending
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<CampusFormValues>({
    resolver: zodResolver(campusSchema),
    defaultValues: { name: campus?.name ?? '', description: campus?.description ?? '', location: campus?.location ?? '' },
  })

  const onSubmit = handleSubmit(async (values) => {
    try {
      if (campus) await update.mutateAsync({ id: campus.id, ...values })
      else await create.mutateAsync(values)
      toast.success(campus ? 'Campus updated' : 'Campus added')
      onDone()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'campus_name_taken') setError('name', { message: err.message })
      else toast.error(errorMessage(err))
    }
  })

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div>
        <Label htmlFor="campus-name">Name</Label>
        <Input id="campus-name" autoFocus aria-invalid={!!errors.name} {...register('name')} />
        <FieldError message={errors.name?.message} />
      </div>
      <div>
        <Label htmlFor="campus-location">Location</Label>
        <Input id="campus-location" placeholder="City or address" {...register('location')} />
        <FieldError message={errors.location?.message} />
      </div>
      <div>
        <Label htmlFor="campus-description">Description</Label>
        <Textarea id="campus-description" rows={3} {...register('description')} />
        <FieldError message={errors.description?.message} />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending}>
          {pending ? 'Saving…' : campus ? 'Save changes' : 'Add campus'}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function CampusFormDialog({ open, onOpenChange, campus }: { open: boolean; onOpenChange: (o: boolean) => void; campus?: Campus }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{campus ? 'Edit campus' : 'Add campus'}</DialogTitle>
          <DialogDescription>A campus groups the NVRs and cameras at one physical site.</DialogDescription>
        </DialogHeader>
        <CampusForm key={campus?.id ?? 'new'} campus={campus} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}
