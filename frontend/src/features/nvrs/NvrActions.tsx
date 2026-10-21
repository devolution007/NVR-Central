import { Plug, RefreshCw, ScanSearch } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/client'
import type { Nvr } from '@/api/types'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { type NvrAction, useNvrAction } from './api'

const ACTIONS: { action: NvrAction; label: string; hint: string; icon: typeof Plug }[] = [
  { action: 'test', label: 'Test connection', hint: 'Checks the login, reads model and clock, and probes what the NVR supports. Cameras are not changed.', icon: Plug },
  { action: 'discover', label: 'Discover cameras', hint: 'Re-reads the camera list. Existing cameras and backup settings are kept.', icon: ScanSearch },
  { action: 'reconnect', label: 'Reconnect', hint: 'Full re-check: connection test plus camera discovery.', icon: RefreshCw },
]

/** Test / discover / reconnect for one NVR. An NVR whose credentials were rejected asks for confirmation first,
 *  because every further wrong attempt counts toward the NVR's own account lockout. */
export function NvrActions({ nvr, labelled = false }: { nvr: Nvr; labelled?: boolean }) {
  const run = useNvrAction()
  const [pendingForce, setPendingForce] = useState<NvrAction | null>(null)
  const busy = nvr.status === 'connecting' || run.isPending
  const disabledReason = !nvr.enabled ? 'Enable this NVR first' : nvr.status === 'connecting' ? 'A check is already running' : undefined

  const start = (action: NvrAction, force = false) =>
    run.mutate(
      { id: nvr.id, action, force },
      {
        onSuccess: () => toast.success('Check started. The status updates automatically.'),
        onError: (err) => toast.error(errorMessage(err)),
        onSettled: () => setPendingForce(null),
      },
    )

  return (
    <>
      <div className="flex items-center gap-1">
        {ACTIONS.map(({ action, label, hint, icon: Icon }) => (
          <Tooltip key={action}>
            <TooltipTrigger asChild>
              <Button
                variant={labelled ? 'outline' : 'ghost'}
                size={labelled ? 'default' : 'icon'}
                aria-label={`${label}: ${nvr.name}`}
                disabled={busy || !nvr.enabled}
                onClick={() => (nvr.status === 'auth_failed' ? setPendingForce(action) : start(action))}
              >
                <Icon className={nvr.status === 'connecting' && action === 'reconnect' ? 'animate-spin' : undefined} />
                {labelled && label}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{disabledReason ?? hint}</TooltipContent>
          </Tooltip>
        ))}
      </div>
      <ConfirmDialog
        open={pendingForce !== null}
        onOpenChange={(o) => !o && setPendingForce(null)}
        title="Try these credentials again?"
        description="The NVR rejected this username and password earlier. Every further wrong attempt counts toward the NVR's own lockout, which can block DMSS and the web interface too. If the password changed, edit the NVR instead."
        confirmLabel="Try anyway"
        pending={run.isPending}
        onConfirm={() => pendingForce && start(pendingForce, true)}
      />
    </>
  )
}
