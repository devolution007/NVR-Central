import type { NvrStatus } from '@/api/types'
import { cn } from '@/lib/utils'

const STATUS: Record<NvrStatus, { label: string; dot: string; text: string; pulse?: boolean; hint: string }> = {
  pending: { label: 'Not tested', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'No connection test has run yet. This is not the same as offline.' },
  connecting: { label: 'Connecting', dot: 'bg-warning', text: 'text-warning', pulse: true, hint: 'A connection attempt is in progress.' },
  connected: { label: 'Connected', dot: 'bg-success', text: 'text-success', hint: 'The last connection test succeeded.' },
  offline: { label: 'Offline', dot: 'bg-destructive', text: 'text-destructive', hint: 'The NVR did not respond.' },
  auth_failed: { label: 'Authentication failed', dot: 'bg-destructive', text: 'text-destructive', hint: 'The NVR rejected the stored username or password.' },
  p2p_unavailable: { label: 'P2P unavailable', dot: 'bg-destructive', text: 'text-destructive', hint: 'The P2P route could not reach this NVR.' },
  partially_supported: { label: 'Partially supported', dot: 'bg-warning', text: 'text-warning', hint: 'Some features are unavailable on this NVR.' },
  unsupported: { label: 'Unsupported', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'This NVR model or firmware is not supported.' },
}

export const NVR_STATUS_LABEL = (s: NvrStatus) => STATUS[s].label

/** One consistent status chip for every NVR list. Colour is never the only signal: the label is always shown. */
export function NvrStatusIndicator({ status, message, className }: { status: NvrStatus; message?: string | null; className?: string }) {
  const s = STATUS[status]
  return (
    <span className={cn('inline-flex items-center gap-2 text-sm font-medium', s.text, className)} title={message ?? s.hint}>
      <span className={cn('size-2 rounded-full', s.dot, s.pulse && 'animate-pulse')} aria-hidden />
      {s.label}
    </span>
  )
}
