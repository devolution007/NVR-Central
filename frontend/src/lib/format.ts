import type { StreamProfile } from '@/api/types'

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never'
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000)
  if (Number.isNaN(seconds)) return 'never'
  if (seconds < 10) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86400)} d ago`
}

/** 300 -> "UTC+05:00", -210 -> "UTC-03:30". */
export function utcOffsetLabel(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return 'Unknown'
  const sign = minutes < 0 ? '-' : '+'
  const abs = Math.abs(minutes)
  return `UTC${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}

export function streamLabel(p: StreamProfile | null): string {
  if (!p) return '—'
  const parts = [
    p.codec,
    p.width && p.height ? `${p.width}×${p.height}` : null,
    p.bitrate_kbps ? `${p.bitrate_kbps} kbps` : null,
    p.fps ? `${p.fps} fps` : null,
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : '—'
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}
