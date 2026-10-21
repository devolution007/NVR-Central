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
  utc_offset_minutes: number | null
  last_checked_at: string | null
  last_success_at: string | null
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
  /** Queue a connection test and camera discovery right after saving (create only). */
  auto_connect?: boolean
}

export interface DashboardSummary {
  campuses: number
  nvrs_total: number
  nvrs_by_status: Record<NvrStatus, number>
  cameras: number
  cameras_with_backup: number
}

export type CameraStatus = 'unknown' | 'online' | 'offline' | 'unavailable'
export type CapabilityStatus = 'supported_tested' | 'unsupported' | 'unknown'
export type CapabilityName =
  | 'p2p_auth'
  | 'device_info'
  | 'channel_discovery'
  | 'live_video'
  | 'recording_search'
  | 'recording_download'
  | 'snapshot'

export interface StreamProfile {
  stream: 'main' | 'sub'
  codec: string | null
  width: number | null
  height: number | null
  bitrate_kbps: number | null
  fps: number | null
}

export interface Camera {
  id: string
  nvr_id: string
  nvr_name: string
  campus_id: string
  campus_name: string
  channel_number: number
  name: string
  model: string | null
  status: CameraStatus
  status_detail: string | null
  status_checked_at: string | null
  last_discovered_at: string | null
  main_stream: StreamProfile | null
  sub_stream: StreamProfile | null
}

export interface Capability {
  name: CapabilityName
  status: CapabilityStatus
  detail: string
  checked_at: string | null
}

export type PeerMode = 'connector' | 'router'
export type PeerStatus = 'connected' | 'stale' | 'never_connected' | 'unknown' | 'revoked'

export interface TunnelHub {
  configured: boolean
  public_key: string | null
  endpoint: string | null
  network: string
  hub_address: string
  notes: string[]
}

export interface TunnelPeer {
  id: string
  campus_id: string
  campus_name: string
  name: string
  mode: PeerMode
  public_key: string
  tunnel_ip: string
  virtual_subnet: string
  lan_cidr: string
  nvr_address_pattern: string
  status: PeerStatus
  last_handshake_at: string | null
  health_checked_at: string | null
  rx_bytes: number | null
  tx_bytes: number | null
  created_at: string
}

export interface PeerInput {
  campus_id: string
  name: string
  mode: PeerMode
  lan_cidr: string
  public_key?: string
}

export interface PeerCreated {
  peer: TunnelPeer
  config: string | null
  private_key_shown_once: boolean
  warnings: string[]
}
