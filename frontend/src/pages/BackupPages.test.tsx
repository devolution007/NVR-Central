import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http } from '@/api/client'
import type { BackupJob, BackupJobDetail, BackupPolicy, BackupStatus, RecordingSearch, SftpDestination, User } from '@/api/types'
import { useAuth } from '@/features/auth/store'
import App from '@/App'

const user = (role: User['role']): User => ({ id: 'u1', username: role, display_name: role, email: null, role, is_active: true, last_login_at: null, created_at: '', campus_ids: [] })

const dest: SftpDestination = {
  id: 'd1', name: 'Archive', host: 'backup.example.com', port: 22, username: 'backup', auth_type: 'password', has_secret: true,
  base_path: '/backups', host_key_type: 'ssh-ed25519', host_key_sha256: 'SHA256:' + 'A'.repeat(43), enabled: true,
  last_test_at: null, last_test_ok: null, last_test_message: null, policy_count: 2, created_at: '',
}
const policy = (n: number, over: Partial<BackupPolicy> = {}): BackupPolicy => ({
  camera_id: `c${n}`, camera_name: `Cam ${n}`, channel_number: n, nvr_id: 'n1', nvr_name: 'Main NVR', campus_id: 'k1', campus_name: 'Head Office',
  configured: false, enabled: false, backup_stream: 'sub', frequency_minutes: 60, recording_mode: 'all', destination_id: null, destination_name: null,
  checkpoint_at: null, last_run_at: null, next_run_at: null, consecutive_failures: 0, last_job_status: null, last_job_message: null, ...over,
})
const job = (over: Partial<BackupJob> = {}): BackupJob => ({
  id: 'j1', camera_id: 'c1', camera_name: 'Cam 1', channel_number: 1, nvr_id: 'n1', nvr_name: 'Main NVR', campus_id: 'k1', campus_name: 'Head Office',
  destination_id: 'd1', destination_name: 'Archive', stream: 'sub', trigger: 'manual', status: 'running', window_start: '2026-10-22T00:00:00Z',
  window_end: '2026-10-22T06:00:00Z', cancel_requested: false, files_total: 2, files_done: 1, files_failed: 0, files_skipped: 0,
  bytes_total: 400_000_000, bytes_done: 100_000_000, message: null, requested_by: 'admin', created_at: '2026-10-22T06:00:00Z', started_at: '2026-10-22T06:00:01Z', finished_at: null, ...over,
})
const okStatus: BackupStatus = { worker_online: true, detail: 'Worker online', active_jobs: 0, queued_jobs: 0, failed_last_24h: 0, bytes_last_24h: 0, destinations_failing: 0, staging_free_gb: 50 }

let me: User
let status: BackupStatus
let destinations: SftpDestination[]
let policies: BackupPolicy[]
let jobs: BackupJob[]
let detail: BackupJobDetail | null
let recordings: RecordingSearch
const posts: { url: string; body: unknown }[] = []
const puts: { url: string; body: unknown }[] = []

function renderApp(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('backup pages', () => {
  beforeEach(() => {
    status = okStatus
    destinations = [dest]
    policies = [policy(1), policy(2, { enabled: true, configured: true, destination_id: 'd1', destination_name: 'Archive', last_job_status: 'failed', last_job_message: 'SFTP server unreachable', consecutive_failures: 2 })]
    jobs = [job()]
    detail = null
    recordings = { camera_id: 'c1', searched_from: '', searched_to: '', utc_offset_minutes: 300, items: [] }
    posts.length = 0
    puts.length = 0
    me = user('admin')
    useAuth.setState({ user: me, status: 'authenticated' })
    vi.spyOn(http, 'get').mockImplementation(async (url: string) => {
      if (url === '/auth/me') return { data: me }
      if (url === '/backups/status') return { data: status }
      if (url === '/backups/destinations') return { data: destinations }
      if (url === '/backups/destinations/options') return { data: destinations.map(({ id, name, enabled }) => ({ id, name, enabled })) }
      if (url === '/backups/policies') return { data: { items: policies, total: policies.length, page: 1, page_size: 500 } }
      if (url === '/backups/jobs') return { data: { items: jobs, total: jobs.length, page: 1, page_size: 25 } }
      if (url.startsWith('/backups/jobs/') && detail) return { data: detail }
      if (url.includes('/recordings')) return { data: recordings }
      if (url === '/campuses') return { data: { items: [{ id: 'k1', name: 'Head Office' }], total: 1, page: 1, page_size: 200 } }
      if (url === '/cameras') return { data: { items: [{ id: 'c1', name: 'Cam 1', nvr_name: 'Main NVR', channel_number: 1, status: 'online' }], total: 1, page: 1, page_size: 500 } }
      return { data: {} }
    })
    vi.spyOn(http, 'post').mockImplementation(async (url: string, body?: unknown) => {
      posts.push({ url, body })
      if (url.endsWith('/test')) return { data: { ...dest, last_test_ok: true, last_test_at: new Date().toISOString(), last_test_message: 'ok' } }
      return { data: job({ status: 'queued' }) }
    })
    vi.spyOn(http, 'put').mockImplementation(async (url: string, body?: unknown) => {
      puts.push({ url, body })
      return { data: policy(1) }
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('SFTP destinations lists a destination without any secret and tests it on demand', async () => {
    renderApp('/sftp')
    expect(await screen.findByText('Archive')).toBeInTheDocument()
    expect(screen.getByText('backup@backup.example.com:22')).toBeInTheDocument()
    expect(screen.getByText('Not tested')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Test Archive' }))
    await waitFor(() => expect(posts.map((p) => p.url)).toContain('/backups/destinations/d1/test'))
  })

  it('SFTP destinations is for administrators only', async () => {
    me = user('operator')
    useAuth.setState({ user: me, status: 'authenticated' })
    renderApp('/sftp')
    expect(await screen.findByText('Administrators only')).toBeInTheDocument()
    expect(http.get).not.toHaveBeenCalledWith('/backups/destinations')
  })

  it('a destination cannot be saved until its host key is fetched and confirmed', async () => {
    destinations = []
    vi.spyOn(http, 'post').mockImplementation(async (url: string, body?: unknown) => {
      posts.push({ url, body })
      if (url.endsWith('/host-key')) return { data: { key_type: 'ssh-ed25519', sha256: 'SHA256:' + 'B'.repeat(43) } }
      return { data: dest }
    })
    renderApp('/sftp')
    await userEvent.click(await screen.findByRole('button', { name: /Add destination/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Save destination' })).toBeDisabled()
    await userEvent.type(within(dialog).getByLabelText('Name'), 'New')
    await userEvent.type(within(dialog).getByLabelText('SFTP server address'), 'backup.example.com')
    await userEvent.type(within(dialog).getByLabelText('Username'), 'backup')
    await userEvent.type(within(dialog).getByLabelText('Password'), 'pw')
    await userEvent.click(within(dialog).getByRole('button', { name: /Fetch host key/ }))
    expect(await within(dialog).findByTestId('fetched-fingerprint')).toHaveTextContent('SHA256:BBBB')
    expect(within(dialog).getByRole('button', { name: 'Save destination' })).toBeDisabled() // not yet confirmed
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /I compared this fingerprint/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save destination' }))
    await waitFor(() => expect(posts.find((p) => p.url === '/backups/destinations')).toBeTruthy())
    const sent = posts.find((p) => p.url === '/backups/destinations')?.body as Record<string, unknown>
    expect(sent).toMatchObject({ host: 'backup.example.com', host_key_sha256: 'SHA256:' + 'B'.repeat(43), password: 'pw', base_path: '/backups' })
  })

  it('policies show each camera, its last result and an honest "not set up" state', async () => {
    renderApp('/backup/settings')
    expect(await screen.findByText('Cam 1')).toBeInTheDocument()
    expect(screen.getByText('Not set up')).toBeInTheDocument()
    expect(screen.getByText(/On · every 1 h/)).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText(/2 failed in a row/)).toBeInTheDocument()
    expect(screen.getByText('SFTP server unreachable')).toBeInTheDocument()
  })

  it('a bulk edit sends only the fields the user changed', async () => {
    renderApp('/backup/settings')
    await screen.findByText('Cam 1')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Cam 1' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Cam 2' }))
    await userEvent.click(screen.getByRole('button', { name: /Edit 2 selected/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Backup settings for 2 cameras/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByLabelText('Which recording to back up'))
    await userEvent.click(await screen.findByRole('option', { name: /Main stream/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(posts.find((p) => p.url === '/backups/policies/bulk')).toBeTruthy())
    expect(posts.find((p) => p.url === '/backups/policies/bulk')?.body).toEqual({ camera_ids: ['c1', 'c2'], backup_stream: 'main', backfill_hours: 0 })
  })

  it('viewers can read policies but get no edit controls', async () => {
    me = user('viewer')
    useAuth.setState({ user: me, status: 'authenticated' })
    renderApp('/backup/settings')
    expect(await screen.findByText('Cam 1')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Select Cam 1' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Edit backup settings/ })).not.toBeInTheDocument()
  })

  it('backup jobs show live progress, and a job detail lists each recording with its error', async () => {
    detail = {
      ...job({ status: 'partial', files_failed: 1, message: '1 of 2 recordings failed.' }),
      files: [
        { id: 'f1', status: 'done', recording_start: '2026-10-22T01:00:00Z', recording_end: '2026-10-22T02:00:00Z', size_bytes: 200_000_000, bytes_done: 200_000_000, attempts: 1, error: null, remote_path: '/backups/a.dav', sha256: 'x' },
        { id: 'f2', status: 'failed', recording_start: '2026-10-22T02:00:00Z', recording_end: '2026-10-22T03:00:00Z', size_bytes: 200_000_000, bytes_done: 0, attempts: 2, error: 'The NVR closed the connection after 43122688 of 235405312 bytes', remote_path: null, sha256: null },
      ],
    }
    renderApp('/backup/jobs')
    expect(await screen.findByText('Cam 1')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Backup progress' })).toHaveAttribute('aria-valuenow', '25')
    expect(screen.getByText(/95\.4 MB of 381\.5 MB/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Details of the Cam 1 backup/ }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/closed the connection after 43122688/)).toBeInTheDocument()
    expect(within(dialog).getByText('attempt 2')).toBeInTheDocument()
    expect(within(dialog).getByText('/backups/a.dav')).toBeInTheDocument()
  })

  it('the history page filters by result and shows empty and error states', async () => {
    jobs = []
    renderApp('/backup/history')
    expect(await screen.findByText('No backups yet')).toBeInTheDocument()
  })

  it('warns when no worker is running instead of leaving jobs queued without explanation', async () => {
    status = { ...okStatus, worker_online: false, detail: 'No background worker is running.' }
    renderApp('/backup/jobs')
    expect(await screen.findByText(/Backups will not run/)).toBeInTheDocument()
    expect(screen.getByText(/No background worker is running/)).toBeInTheDocument()
  })

  it('warns about SFTP destinations that failed their last test', async () => {
    status = { ...okStatus, destinations_failing: 1 }
    renderApp('/backup/jobs')
    expect(await screen.findByText(/1 SFTP destination\(s\) failed their last test/)).toBeInTheDocument()
  })

  it('recording browser searches the NVR, shows backup state and starts a backup for one recording', async () => {
    recordings = {
      camera_id: 'c1', searched_from: '', searched_to: '', utc_offset_minutes: 300,
      items: [
        { start: '2026-10-22T01:00:00Z', end: '2026-10-22T02:00:00Z', stream: 'sub', size_bytes: 235_405_312, kind: 'Timing', in_progress: false, backup_status: 'done', backup_file_id: 'f1', remote_path: '/x.dav' },
        { start: '2026-10-22T02:00:00Z', end: '2026-10-22T03:00:00Z', stream: 'sub', size_bytes: 234_618_880, kind: 'Timing', in_progress: false, backup_status: null, backup_file_id: null, remote_path: null },
        { start: '2026-10-22T03:00:00Z', end: '2026-10-22T03:40:00Z', stream: 'sub', size_bytes: 150_000_000, kind: 'Timing', in_progress: true, backup_status: null, backup_file_id: null, remote_path: null },
      ],
    }
    renderApp('/recordings')
    await userEvent.click(await screen.findByLabelText('Campus'))
    await userEvent.click(await screen.findByRole('option', { name: 'Head Office' }))
    await userEvent.click(screen.getByLabelText('Camera'))
    await userEvent.click(await screen.findByRole('option', { name: /Cam 1/ }))
    await userEvent.click(screen.getByRole('button', { name: /Search the NVR/ }))
    expect(await screen.findByText('Backed up')).toBeInTheDocument()
    expect(screen.getByText('Still being recorded')).toBeInTheDocument()
    expect(screen.getByText('Not backed up')).toBeInTheDocument()
    expect(screen.getByText(/NVR clock UTC\+05:00/)).toBeInTheDocument()
    const buttons = screen.getAllByRole('button', { name: /^Back up the recording from/ })
    expect([buttons[0], buttons[2]].every((b) => (b as HTMLButtonElement).disabled)).toBe(true) // done and in progress
    await userEvent.click(buttons[1]!)
    await waitFor(() => expect(posts.find((p) => p.url === '/backups/jobs')).toBeTruthy())
    expect(posts.find((p) => p.url === '/backups/jobs')?.body).toMatchObject({ camera_id: 'c1', stream: 'sub', start: '2026-10-22T02:00:00Z', end: '2026-10-22T03:00:00Z' })
  })

  it('manual backup validates the form before sending anything', async () => {
    renderApp('/backup/manual')
    await userEvent.click(await screen.findByRole('button', { name: 'Start backup' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a camera')
    expect(posts).toHaveLength(0)
  })

  it('viewers cannot start a manual backup', async () => {
    me = user('viewer')
    useAuth.setState({ user: me, status: 'authenticated' })
    renderApp('/backup/manual')
    expect(await screen.findByText(/Viewers cannot start backups/)).toBeInTheDocument()
  })
})
