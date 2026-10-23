import type { BackupPolicy, BackupStream, PolicyInput, RecordingMode } from '@/api/types'

export const KEEP = 'keep'

/** Form state for one policy, or for a bulk edit where "keep" leaves each camera's own value alone. */
export interface Draft {
  enabled: string
  stream: string
  frequency: string
  mode: string
  destination: string
  backfill: string
}

export function initial(policy: BackupPolicy | undefined, bulk: boolean): Draft {
  if (bulk) return { enabled: KEEP, stream: KEEP, frequency: KEEP, mode: KEEP, destination: KEEP, backfill: '0' }
  return {
    enabled: policy?.enabled ? 'on' : 'off',
    stream: policy?.backup_stream ?? 'sub',
    frequency: String(policy?.frequency_minutes ?? 60),
    mode: policy?.recording_mode ?? 'all',
    destination: policy?.destination_id ?? '',
    backfill: '0',
  }
}

export function draftToInput(d: Draft): { input: PolicyInput; error?: string } {
  const input: PolicyInput = {}
  if (d.enabled !== KEEP) input.enabled = d.enabled === 'on'
  if (d.stream !== KEEP) input.backup_stream = d.stream as BackupStream
  if (d.frequency !== KEEP) input.frequency_minutes = Number(d.frequency)
  if (d.mode !== KEEP) input.recording_mode = d.mode as RecordingMode
  if (d.destination !== KEEP) input.destination_id = d.destination || null
  const hours = Number(d.backfill)
  if (!Number.isInteger(hours) || hours < 0 || hours > 168) return { input, error: 'Start-from must be a whole number of hours from 0 to 168' }
  input.backfill_hours = hours
  if (input.enabled && !input.destination_id && d.destination !== KEEP) return { input, error: 'Choose a destination before turning backups on' }
  return { input }
}

