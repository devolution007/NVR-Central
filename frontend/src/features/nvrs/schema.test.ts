import { campusSchema, makeNvrSchema } from './schema'

const base = {
  campus_id: 'c1',
  name: 'Main NVR',
  connection_type: 'lan' as const,
  host: '192.168.15.6',
  http_port: 80,
  rtsp_port: 554,
  username: 'admin',
  password: 'secret',
  p2p_serial: '',
  enabled: true,
}

const issues = (r: ReturnType<ReturnType<typeof makeNvrSchema>['safeParse']>) =>
  r.success ? [] : r.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`)

describe('NVR form schema', () => {
  it('accepts a complete LAN profile', () => {
    expect(makeNvrSchema('create').safeParse(base).success).toBe(true)
  })

  it('requires an address for tunnel and LAN connections', () => {
    for (const connection_type of ['tunnel', 'lan'] as const) {
      const r = makeNvrSchema('create').safeParse({ ...base, connection_type, host: '' })
      expect(issues(r).some((m) => m.startsWith('host'))).toBe(true)
    }
  })

  it.each(['http://10.0.0.1', '10.0.0.1:80', 'a b', 'host/path', '-bad.example'])('rejects host %s', (host) => {
    expect(issues(makeNvrSchema('create').safeParse({ ...base, host })).some((m) => m.startsWith('host'))).toBe(true)
  })

  it.each(['10.0.0.5', 'nvr-1.campus.example.com', 'nvr1'])('accepts host %s', (host) => {
    expect(makeNvrSchema('create').safeParse({ ...base, host }).success).toBe(true)
  })

  it('requires a serial only for P2P, and no host', () => {
    const p2p = { ...base, connection_type: 'p2p' as const, host: '' }
    expect(issues(makeNvrSchema('create').safeParse(p2p)).some((m) => m.startsWith('p2p_serial'))).toBe(true)
    expect(makeNvrSchema('create').safeParse({ ...p2p, p2p_serial: 'ABCD1234' }).success).toBe(true)
  })

  it('requires a password to create but not to edit (blank keeps the stored one)', () => {
    expect(issues(makeNvrSchema('create').safeParse({ ...base, password: '' })).some((m) => m.startsWith('password'))).toBe(true)
    expect(makeNvrSchema('edit').safeParse({ ...base, password: '' }).success).toBe(true)
  })

  it('validates port ranges and NaN from empty number inputs', () => {
    expect(makeNvrSchema('create').safeParse({ ...base, http_port: 0 }).success).toBe(false)
    expect(makeNvrSchema('create').safeParse({ ...base, rtsp_port: 70000 }).success).toBe(false)
    expect(makeNvrSchema('create').safeParse({ ...base, http_port: Number.NaN }).success).toBe(false)
  })
})

describe('campus schema', () => {
  it('trims and rejects blank names', () => {
    expect(campusSchema.safeParse({ name: '   ', description: '', location: '' }).success).toBe(false)
    const ok = campusSchema.safeParse({ name: '  North  ', description: '', location: '' })
    expect(ok.success && ok.data.name).toBe('North')
  })
})
