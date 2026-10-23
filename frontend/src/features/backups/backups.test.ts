import { makeDestinationSchema } from './destinationSchema'
import { localInputToIso, toLocalInput } from './datetime'
import { draftToInput, initial, KEEP } from './policyDraft'
import { progressPercent } from './progress'

const base = {
  name: 'Archive',
  host: 'backup.example.com',
  port: 22,
  username: 'backup',
  auth_type: 'password' as const,
  password: 'pw',
  private_key: '',
  base_path: '/backups',
  enabled: true,
}
const issues = (r: { success: boolean; error?: { issues: { path: PropertyKey[]; message: string }[] } }) =>
  r.success ? [] : (r.error?.issues.map((i) => `${String(i.path[0])}: ${i.message}`) ?? [])

describe('destination form schema', () => {
  it('accepts a normal password destination', () => {
    expect(makeDestinationSchema('create').safeParse(base).success).toBe(true)
  })

  it('rejects schemes, ports and paths in the address', () => {
    for (const host of ['sftp://backup.example.com', 'backup.example.com:22', 'a/b']) {
      expect(makeDestinationSchema('create').safeParse({ ...base, host }).success).toBe(false)
    }
  })

  it('needs a password to create but not to edit (blank keeps the stored one)', () => {
    expect(issues(makeDestinationSchema('create').safeParse({ ...base, password: '' }))).toContain('password: Password is required')
    expect(makeDestinationSchema('edit').safeParse({ ...base, password: '' }).success).toBe(true)
  })

  it('needs a pasted key for key login on create', () => {
    const key = { ...base, auth_type: 'key' as const, password: '' }
    expect(issues(makeDestinationSchema('create').safeParse(key))).toContain('private_key: Paste the private key')
    expect(makeDestinationSchema('create').safeParse({ ...key, private_key: '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END' }).success).toBe(true)
  })

  it('validates the port and refuses ".." in the base folder', () => {
    expect(makeDestinationSchema('create').safeParse({ ...base, port: 0 }).success).toBe(false)
    expect(makeDestinationSchema('create').safeParse({ ...base, port: Number.NaN }).success).toBe(false)
    expect(makeDestinationSchema('create').safeParse({ ...base, base_path: '/a/../b' }).success).toBe(false)
  })
})

describe('policy draft', () => {
  it('a bulk draft keeps every setting unless the user changes it', () => {
    const d = initial(undefined, true)
    expect(Object.values({ ...d, backfill: '' }).filter((v) => v === KEEP)).toHaveLength(5)
    expect(draftToInput(d)).toEqual({ input: { backfill_hours: 0 } }) // nothing else is sent
  })

  it('sends only the changed fields in a bulk edit', () => {
    const { input } = draftToInput({ ...initial(undefined, true), stream: 'main', mode: 'continuous' })
    expect(input).toEqual({ backup_stream: 'main', recording_mode: 'continuous', backfill_hours: 0 })
  })

  it('refuses to turn backups on without a destination', () => {
    const d = { ...initial(undefined, false), enabled: 'on' }
    expect(draftToInput(d).error).toMatch(/destination/i)
    expect(draftToInput({ ...d, destination: 'dest-1' }).input).toMatchObject({ enabled: true, destination_id: 'dest-1', backup_stream: 'sub', frequency_minutes: 60 })
  })

  it('validates the start-from hours', () => {
    const d = { ...initial(undefined, true), backfill: '200' }
    expect(draftToInput(d).error).toMatch(/0 to 168/)
    expect(draftToInput({ ...d, backfill: '1.5' }).error).toBeTruthy()
  })
})

describe('progress and time helpers', () => {
  it('computes a clamped percentage and never divides by zero', () => {
    expect(progressPercent(50, 200)).toBe(25)
    expect(progressPercent(300, 200)).toBe(100)
    expect(progressPercent(5, 0)).toBe(0)
    expect(progressPercent(-5, 100)).toBe(0)
  })

  it('round-trips a browser-local datetime to UTC', () => {
    const local = toLocalInput(new Date(2026, 9, 22, 14, 30))
    expect(local).toBe('2026-10-22T14:30')
    expect(localInputToIso(local)).toBe(new Date(2026, 9, 22, 14, 30).toISOString())
  })
})
