import { peerSchema } from './ConnectivityPage'

const ok = { campus_id: 'c1', name: 'HQ', mode: 'connector' as const, lan_cidr: '192.168.15.0/24', public_key: '' }

describe('peer form schema', () => {
  it('accepts a /24 LAN with or without a supplied public key', () => {
    expect(peerSchema.safeParse(ok).success).toBe(true)
    expect(peerSchema.safeParse({ ...ok, mode: 'router', public_key: 'abc' }).success).toBe(true)
  })

  it.each(['192.168.15.0/23', '192.168.15.5/24', 'not-a-network', '192.168.15.0', '10.0.0.0/8', ''])('rejects LAN %s', (lan_cidr) => {
    expect(peerSchema.safeParse({ ...ok, lan_cidr }).success).toBe(false)
  })

  it('requires a campus and a name', () => {
    expect(peerSchema.safeParse({ ...ok, campus_id: '' }).success).toBe(false)
    expect(peerSchema.safeParse({ ...ok, name: '  ' }).success).toBe(false)
  })
})
