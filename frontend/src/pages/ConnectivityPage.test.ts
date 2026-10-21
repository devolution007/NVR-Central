import { peerSchema } from './ConnectivityPage'

const base = {
  campus_id: 'c1',
  name: 'HQ',
  mode: 'connector' as const,
  lan_cidr: '192.168.15.0/24',
  public_key: '',
  nvr_host: '',
  nvr_http_port: 80,
  nvr_rtsp_port: 554,
}
const win = { ...base, mode: 'windows' as const, lan_cidr: '', nvr_host: '192.168.15.6' }

const fields = (r: ReturnType<typeof peerSchema.safeParse>) => (r.success ? [] : r.error.issues.map((i) => i.path.join('.')))

describe('peer form schema: connector and router', () => {
  it('accepts a /24 LAN with or without a supplied public key', () => {
    expect(peerSchema.safeParse(base).success).toBe(true)
    expect(peerSchema.safeParse({ ...base, mode: 'router', public_key: 'abc' }).success).toBe(true)
  })

  it.each(['192.168.15.0/23', '192.168.15.5/24', 'not-a-network', '192.168.15.0', '10.0.0.0/8', ''])('rejects LAN %s', (lan_cidr) => {
    expect(fields(peerSchema.safeParse({ ...base, lan_cidr }))).toContain('lan_cidr')
  })

  it('requires a campus and a name', () => {
    expect(peerSchema.safeParse({ ...base, campus_id: '' }).success).toBe(false)
    expect(peerSchema.safeParse({ ...base, name: '  ' }).success).toBe(false)
  })

  it('does not ask for an NVR address', () => {
    expect(peerSchema.safeParse({ ...base, nvr_host: '' }).success).toBe(true)
  })
})

describe('peer form schema: Windows PC', () => {
  it('needs only the NVR address: the LAN range is optional', () => {
    expect(peerSchema.safeParse(win).success).toBe(true)
    expect(peerSchema.safeParse({ ...win, lan_cidr: '192.168.15.0/24' }).success).toBe(true)
  })

  it.each(['', 'nvr.local', '192.168.15', "192.168.15.6'; rm -rf /", '192.168.15.6/24'])('rejects NVR address %j', (nvr_host) => {
    expect(fields(peerSchema.safeParse({ ...win, nvr_host }))).toContain('nvr_host')
  })

  it('still validates a LAN range when one is given', () => {
    expect(fields(peerSchema.safeParse({ ...win, lan_cidr: '192.168.15.0/16' }))).toContain('lan_cidr')
  })

  it.each([0, 65536, Number.NaN])('rejects forwarded port %s', (p) => {
    expect(peerSchema.safeParse({ ...win, nvr_http_port: p }).success).toBe(false)
    expect(peerSchema.safeParse({ ...win, nvr_rtsp_port: p }).success).toBe(false)
  })
})
