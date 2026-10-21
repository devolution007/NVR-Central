import type { CameraStatus, CapabilityStatus, PeerStatus } from '@/api/types'
import { cn } from '@/lib/utils'

interface Look {
  label: string
  dot: string
  text: string
  hint: string
}

function Chip({ look, className }: { look: Look; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-sm font-medium', look.text, className)} title={look.hint}>
      <span className={cn('size-2 rounded-full', look.dot)} aria-hidden />
      {look.label}
    </span>
  )
}

const CAMERA: Record<CameraStatus, Look> = {
  unknown: { label: 'Not checked', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'No stream check has run for this camera yet.' },
  online: { label: 'Online', dot: 'bg-success', text: 'text-success', hint: 'The NVR answered a stream request for this channel.' },
  offline: { label: 'Not responding', dot: 'bg-destructive', text: 'text-destructive', hint: 'Configured on the NVR, but its stream did not answer.' },
  unavailable: { label: 'Removed from NVR', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'No longer reported by the NVR. History is kept.' },
}

export const CameraStatusIndicator = ({ status, detail }: { status: CameraStatus; detail?: string | null }) => (
  <Chip look={detail ? { ...CAMERA[status], hint: detail } : CAMERA[status]} />
)

const CAPABILITY: Record<CapabilityStatus, Look> = {
  supported_tested: { label: 'Verified', dot: 'bg-success', text: 'text-success', hint: 'Observed working on this NVR.' },
  unsupported: { label: 'Not supported', dot: 'bg-destructive', text: 'text-destructive', hint: 'This NVR does not offer it.' },
  unknown: { label: 'Not verified', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'Not tested yet. This is not the same as unsupported.' },
}

export const CapabilityIndicator = ({ status }: { status: CapabilityStatus }) => <Chip look={CAPABILITY[status]} />

const PEER: Record<PeerStatus, Look> = {
  connected: { label: 'Connected', dot: 'bg-success', text: 'text-success', hint: 'A recent WireGuard handshake was seen.' },
  stale: { label: 'No recent handshake', dot: 'bg-warning', text: 'text-warning', hint: 'The tunnel was up before but has been silent for over 3 minutes.' },
  never_connected: { label: 'Never connected', dot: 'bg-warning', text: 'text-warning', hint: 'The hub has never completed a handshake with this peer.' },
  unknown: { label: 'Unknown', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'Tunnel health is not being collected (no hub reachable from the worker). This is not the same as down.' },
  revoked: { label: 'Revoked', dot: 'bg-muted-foreground', text: 'text-muted-foreground', hint: 'This peer was revoked.' },
}

export const PeerStatusIndicator = ({ status }: { status: PeerStatus }) => <Chip look={PEER[status]} />
