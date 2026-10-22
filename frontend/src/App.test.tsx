import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { http } from '@/api/client'
import type { Nvr } from '@/api/types'
import { useAuth } from '@/features/auth/store'
import App from './App'

const admin = { id: 'u1', username: 'admin', display_name: 'Admin', email: null, role: 'admin' as const, is_active: true, last_login_at: null, created_at: '', campus_ids: [] }
const nvr: Nvr = {
  id: 'n1', campus_id: 'c1', campus_name: 'Head Office', name: 'Main NVR', connection_type: 'lan', host: '10.0.0.5', http_port: 80,
  rtsp_port: 554, username: 'admin', has_password: true, p2p_serial_masked: null, status: 'connected', status_message: null,
  model: 'DHI-NVR', firmware: '1', enabled: true, camera_count: 2, utc_offset_minutes: 300, last_checked_at: null,
  last_success_at: null, created_at: '', updated_at: '',
}

/** Renders the real routed app (providers and all), so a missing root provider fails here and not in the browser. */
function renderApp(path: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App shell with real providers', () => {
  beforeEach(() => {
    useAuth.setState({ user: admin, status: 'authenticated' })
    vi.spyOn(http, 'get').mockImplementation(async (url: string) => {
      if (url === '/auth/me') return { data: admin }
      if (url === '/nvrs') return { data: { items: [nvr], total: 1, page: 1, page_size: 50 } }
      if (url === '/campuses') return { data: { items: [], total: 0, page: 1, page_size: 200 } }
      if (url.endsWith('/capabilities')) return { data: [] }
      if (url.endsWith('/cameras')) return { data: [] }
      if (url.startsWith('/nvrs/')) return { data: nvr }
      if (url === '/cameras') return { data: { items: [], total: 0, page: 1, page_size: 500 } }
      if (url === '/tunnel/hub') return { data: { configured: false, public_key: null, endpoint: null, network: '10.77.0.0/16', hub_address: '10.77.0.1', notes: [] } }
      if (url === '/tunnel/peers') return { data: [] }
      if (url === '/layouts') return { data: [] }
      return { data: {} }
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('NVR list renders its row with test/discover/reconnect actions', async () => {
    renderApp('/nvrs')
    expect(await screen.findByRole('link', { name: 'Main NVR' })).toHaveAttribute('href', '/nvrs/n1')
    expect(screen.getByRole('button', { name: /Test connection: Main NVR/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Discover cameras: Main NVR/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Reconnect: Main NVR/ })).toBeInTheDocument()
  })

  it('NVR detail page renders status, actions and an empty camera state', async () => {
    renderApp('/nvrs/n1')
    expect(await screen.findByRole('heading', { name: 'Main NVR' })).toBeInTheDocument()
    expect(screen.getByText('UTC+05:00')).toBeInTheDocument()
    expect(await screen.findByText('No cameras discovered yet')).toBeInTheDocument()
  })

  it('camera inventory and connectivity pages render without crashing', async () => {
    const { unmount } = renderApp('/cameras')
    expect(await screen.findByText('No cameras discovered yet')).toBeInTheDocument()
    unmount()
    renderApp('/connectivity')
    expect(await screen.findByText('No campus tunnels yet')).toBeInTheDocument()
    expect(screen.getByText(/Not verified on hardware yet/)).toBeInTheDocument()
  })

  it('live pages load on demand and render their empty states', async () => {
    const { unmount } = renderApp('/live')
    expect(await screen.findByRole('heading', { name: 'Live Monitoring' })).toBeInTheDocument()
    expect(await screen.findByText('No cameras to show')).toBeInTheDocument()
    unmount()
    const second = renderApp('/live/favorites')
    expect(await screen.findByRole('heading', { name: 'Favorite Cameras' })).toBeInTheDocument()
    expect(await screen.findByText('No favorite cameras yet')).toBeInTheDocument()
    second.unmount()
    renderApp('/live/layouts')
    expect(await screen.findByRole('heading', { name: 'Saved Layouts' })).toBeInTheDocument()
    expect(await screen.findByText('No saved layouts yet')).toBeInTheDocument()
  })
})
