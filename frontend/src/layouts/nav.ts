import {
  Activity,
  Bell,
  Building2,
  Camera,
  DatabaseBackup,
  Film,
  HardDrive,
  LayoutDashboard,
  type LucideIcon,
  Server,
  Settings,
  Users,
  Video,
} from 'lucide-react'

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  /** Phase in which the page is built. Items without one are live today. */
  phase?: number
  adminOnly?: boolean
}

export interface NavGroup {
  label?: string
  items: NavItem[]
}

export const NAV: NavGroup[] = [
  { items: [{ label: 'Dashboard', to: '/', icon: LayoutDashboard }] },
  {
    label: 'Live Monitoring',
    items: [
      { label: 'All Cameras', to: '/live', icon: Video, phase: 3 },
      { label: 'Favorites', to: '/live/favorites', icon: Video, phase: 3 },
      { label: 'Saved Layouts', to: '/live/layouts', icon: Video, phase: 3 },
    ],
  },
  {
    items: [
      { label: 'Campuses', to: '/campuses', icon: Building2 },
      { label: 'NVR Management', to: '/nvrs', icon: Server },
      { label: 'Camera Management', to: '/cameras', icon: Camera },
      { label: 'Recording Browser', to: '/recordings', icon: Film, phase: 4 },
    ],
  },
  {
    label: 'Backup Management',
    items: [
      { label: 'Auto Backup Settings', to: '/backup/settings', icon: DatabaseBackup, phase: 4 },
      { label: 'Manual Backup', to: '/backup/manual', icon: DatabaseBackup, phase: 4 },
      { label: 'Backup Jobs', to: '/backup/jobs', icon: Activity, phase: 4 },
      { label: 'Backup History', to: '/backup/history', icon: Activity, phase: 4 },
    ],
  },
  {
    items: [
      { label: 'SFTP Destinations', to: '/sftp', icon: HardDrive, phase: 4 },
      { label: 'Users & Permissions', to: '/users', icon: Users, phase: 5, adminOnly: true },
      { label: 'Notifications', to: '/notifications', icon: Bell, phase: 5 },
      { label: 'System Settings', to: '/settings', icon: Settings, phase: 5 },
    ],
  },
]

export const PLANNED_PAGES = NAV.flatMap((g) => g.items).filter((i) => i.phase !== undefined)
