import { formatBytes, streamLabel, timeAgo, utcOffsetLabel } from './format'

describe('format helpers', () => {
  it('renders UTC offsets including negative and half-hour zones', () => {
    expect(utcOffsetLabel(300)).toBe('UTC+05:00')
    expect(utcOffsetLabel(-210)).toBe('UTC-03:30')
    expect(utcOffsetLabel(345)).toBe('UTC+05:45')
    expect(utcOffsetLabel(0)).toBe('UTC+00:00')
    expect(utcOffsetLabel(null)).toBe('Unknown')
  })

  it('describes how long ago something happened', () => {
    const now = new Date('2026-10-21T12:00:00Z')
    expect(timeAgo(null, now)).toBe('never')
    expect(timeAgo('2026-10-21T11:59:58Z', now)).toBe('just now')
    expect(timeAgo('2026-10-21T11:59:15Z', now)).toBe('45s ago')
    expect(timeAgo('2026-10-21T11:50:00Z', now)).toBe('10 min ago')
    expect(timeAgo('2026-10-21T09:00:00Z', now)).toBe('3 h ago')
    expect(timeAgo('2026-10-18T12:00:00Z', now)).toBe('3 d ago')
  })

  it('summarises a stream profile and tolerates missing parts', () => {
    expect(streamLabel({ stream: 'main', codec: 'H.265', width: 1920, height: 1080, bitrate_kbps: 2048, fps: 25 })).toBe('H.265 · 1920×1080 · 2048 kbps · 25 fps')
    expect(streamLabel({ stream: 'sub', codec: 'H.264', width: null, height: null, bitrate_kbps: null, fps: null })).toBe('H.264')
    expect(streamLabel(null)).toBe('—')
  })

  it('formats byte counts', () => {
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
  })
})
