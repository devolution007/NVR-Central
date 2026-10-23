// Phase 4: backups. Re-exported from types.ts so the rest of the app imports everything from '@/api/types'.
import type { StreamKind } from './types'

export type SftpAuthType = 'password' | 'key'
export type BackupStream = 'main' | 'sub'
export type RecordingMode = 'all' | 'continuous' | 'motion' | 'event'
export type BackupJobStatus = 'queued' | 'running' | 'success' | 'partial' | 'failed' | 'cancelled'
export type BackupFileStatus = 'pending' | 'downloading' | 'uploading' | 'verifying' | 'done' | 'failed' | 'skipped' | 'cancelled'
export type BackupTrigger = 'scheduled' | 'manual'

export interface SftpDestination {
  id: string
  name: string
  host: string
  port: number
  username: string
  auth_type: SftpAuthType
  has_secret: boolean
  base_path: string
  host_key_type: string | null
  host_key_sha256: string | null
  enabled: boolean
  last_test_at: string | null
  last_test_ok: boolean | null
  last_test_message: string | null
  policy_count: number
  created_at: string
}

export interface DestinationOption {
  id: string
  name: string
  enabled: boolean
}

export interface DestinationInput {
  name: string
  host: string
  port: number
  username: string
  auth_type: SftpAuthType
  password?: string
  private_key?: string
  base_path: string
  host_key_sha256: string
  host_key_type?: string | null
  enabled: boolean
}

export interface HostKey {
  key_type: string
  sha256: string
}

export interface BackupPolicy {
  camera_id: string
  camera_name: string
  channel_number: number
  nvr_id: string
  nvr_name: string
  campus_id: string
  campus_name: string
  configured: boolean
  enabled: boolean
  backup_stream: BackupStream
  frequency_minutes: number
  recording_mode: RecordingMode
  destination_id: string | null
  destination_name: string | null
  checkpoint_at: string | null
  last_run_at: string | null
  next_run_at: string | null
  consecutive_failures: number
  last_job_status: BackupJobStatus | null
  last_job_message: string | null
}

export interface PolicyInput {
  enabled?: boolean
  backup_stream?: BackupStream
  frequency_minutes?: number
  recording_mode?: RecordingMode
  destination_id?: string | null
  backfill_hours?: number
}

export interface RecordingItem {
  start: string
  end: string
  stream: StreamKind
  size_bytes: number | null
  kind: string
  in_progress: boolean
  backup_status: BackupFileStatus | null
  backup_file_id: string | null
  remote_path: string | null
}

export interface RecordingSearch {
  camera_id: string
  searched_from: string
  searched_to: string
  utc_offset_minutes: number
  items: RecordingItem[]
}

export interface BackupJob {
  id: string
  camera_id: string
  camera_name: string
  channel_number: number
  nvr_id: string
  nvr_name: string
  campus_id: string
  campus_name: string
  destination_id: string
  destination_name: string
  stream: BackupStream
  trigger: BackupTrigger
  status: BackupJobStatus
  window_start: string
  window_end: string
  cancel_requested: boolean
  files_total: number
  files_done: number
  files_failed: number
  files_skipped: number
  bytes_total: number
  bytes_done: number
  message: string | null
  requested_by: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface BackupFileRow {
  id: string
  status: BackupFileStatus
  recording_start: string
  recording_end: string
  size_bytes: number | null
  bytes_done: number
  attempts: number
  error: string | null
  remote_path: string | null
  sha256: string | null
}

export interface BackupJobDetail extends BackupJob {
  files: BackupFileRow[]
}

export interface JobCreateInput {
  camera_id: string
  stream?: BackupStream
  destination_id?: string
  start: string
  end: string
}

export interface BackupStatus {
  worker_online: boolean | null
  detail: string
  active_jobs: number
  queued_jobs: number
  failed_last_24h: number
  bytes_last_24h: number
  destinations_failing: number
  staging_free_gb: number | null
}
