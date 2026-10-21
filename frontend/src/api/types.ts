export type Role = 'admin' | 'operator' | 'viewer'
export type ConnectionType = 'tunnel' | 'lan' | 'p2p'
export type NvrStatus =
  | 'pending'
  | 'connecting'
  | 'connected'
  | 'offline'
  | 'auth_failed'
  | 'p2p_unavailable'
  | 'partially_supported'
  | 'unsupported'

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface User {
  id: string
  username: string
  display_name: string
  email: string | null
  role: Role
  is_active: boolean
  last_login_at: string | null
  created_at: string
  campus_ids: string[]
}

export interface Campus {
  id: string
  name: string
  description: string
  location: string
  created_at: string
  updated_at: string
  nvr_count: number
  connected_nvr_count: number
  camera_count: number
  backup_enabled_camera_count: number
}

export interface CampusInput {
  name: string
  description: string
  location: string
}

export interface Nvr {
  id: string
  campus_id: string
  campus_name: string
  name: string
  connection_type: ConnectionType
  host: string | null
  http_port: number
  rtsp_port: number
  username: string
  has_password: boolean
  p2p_serial_masked: string | null
  status: NvrStatus
  status_message: string | null
  model: string | null
  firmware: string | null
  enabled: boolean
  camera_count: number
  created_at: string
  updated_at: string
}

export interface NvrInput {
  campus_id: string
  name: string
  connection_type: ConnectionType
  host: string | null
  http_port: number
  rtsp_port: number
  username: string
  password?: string
  p2p_serial?: string
  enabled: boolean
}

export interface DashboardSummary {
  campuses: number
  nvrs_total: number
  nvrs_by_status: Record<NvrStatus, number>
  cameras: number
  cameras_with_backup: number
}
