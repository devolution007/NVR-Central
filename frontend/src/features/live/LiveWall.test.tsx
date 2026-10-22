import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { http } from '@/api/client'
import type { Camera, Layout } from '@/api/types'
import { LiveWall, type WallSource } from './LiveWall'

// The player is covered by its own tests; here a tile is just a labelled box so counts and order are easy to assert.
vi.mock('./LiveCameraPlayer', () => ({
  LiveCameraPlayer: ({ camera, paused, onFullscreen }: { camera: Camera; paused?: boolean; onFullscreen?: () => void }) => (
    <div data-testid="tile" data-paused={String(!!paused)}>
      {camera.name}
      <button onClick={onFullscreen}>fs-{camera.name}</button>
    </div>
  ),
}))
vi.mock('./CameraFullscreenDialog', () => ({
  CameraFullscreenDialog: ({ camera }: { camera: Camera | null }) => (camera ? <div role="dialog">fullscreen {camera.name}</div> : null),
}))

const cam = (i: number, over: Partial<Camera> = {}): Camera => ({
  id: `c${i}`, nvr_id: 'n1', nvr_name: 'NVR', campus_id: 'k1', campus_name: 'Campus', channel_number: i, name: `Cam ${i}`, model: null,
  status: 'online', status_detail: null, status_checked_at: null, last_discovered_at: null, main_stream: null, sub_stream: null, is_favorite: false, ...over,
})
const page = <T,>(items: T[]) => ({ data: { items, total: items.length, page: 1, page_size: 500 } })

let cameras: Camera[] = []
let layouts: Layout[] = []
const getCalls: { url: string; params?: Record<string, unknown> }[] = []

function LocationProbe() {
  const l = useLocation()
  return <output data-testid="loc">{l.pathname + l.search}</output>
}

function renderWall(source: WallSource = 'all', url = '/live') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={[url]}>
        <LiveWall source={source} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('LiveWall', () => {
  beforeEach(() => {
    cameras = Array.from({ length: 12 }, (_, i) => cam(i + 1))
    layouts = []
    getCalls.length = 0
    vi.spyOn(http, 'get').mockImplementation(async (url: string, cfg?: { params?: Record<string, unknown> }) => {
      getCalls.push({ url, params: cfg?.params })
      if (url === '/cameras') return page(cameras)
      if (url === '/layouts') return { data: layouts }
      return page([])
    })
  })
  afterEach(() => vi.restoreAllMocks())

  const tiles = () => screen.queryAllByTestId('tile').map((t) => t.textContent?.replace(/fs-.*/, ''))

  it('shows 4 cameras by default and pages through the rest', async () => {
    renderWall()
    await waitFor(() => expect(tiles()).toHaveLength(4))
    expect(tiles()).toEqual(['Cam 1', 'Cam 2', 'Cam 3', 'Cam 4'])
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(tiles()).toEqual(['Cam 5', 'Cam 6', 'Cam 7', 'Cam 8']))
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(tiles()).toHaveLength(4))
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
  })

  it.each([1, 4, 9, 16, 25] as const)('layout size %s shows that many tiles when enough cameras exist', async (size) => {
    cameras = Array.from({ length: 30 }, (_, i) => cam(i + 1))
    renderWall('all', `/live?grid=${size}`)
    await waitFor(() => expect(tiles()).toHaveLength(size))
    expect(screen.getByRole('button', { name: String(size) })).toHaveAttribute('aria-pressed', 'true')
  })

  it('changing the layout size updates the view and the URL, and goes back to page 1', async () => {
    renderWall('all', '/live?page=2')
    await waitFor(() => expect(tiles()).toHaveLength(4))
    await userEvent.click(screen.getByRole('button', { name: '9' }))
    await waitFor(() => expect(tiles()).toHaveLength(9))
    expect(screen.getByTestId('loc').textContent).toContain('grid=9')
    expect(screen.getByTestId('loc').textContent).not.toContain('page=')
  })

  it('an invalid grid size in the URL falls back to 4', async () => {
    renderWall('all', '/live?grid=7')
    await waitFor(() => expect(tiles()).toHaveLength(4))
  })

  it('hides cameras that are not responding by default, and removed cameras always', async () => {
    cameras = [cam(1), cam(2, { status: 'offline' }), cam(3, { status: 'unavailable' }), cam(4, { status: 'unknown' })]
    renderWall()
    await waitFor(() => expect(tiles()).toEqual(['Cam 1', 'Cam 4']))
    await userEvent.click(screen.getByRole('checkbox', { name: /Hide cameras not responding/ }))
    await waitFor(() => expect(tiles()).toEqual(['Cam 1', 'Cam 2', 'Cam 4']))
  })

  it('passes the campus, NVR and search filters to the API', async () => {
    renderWall('all', '/live?campus=k1&nvr=n9&q=gate')
    await waitFor(() => expect(tiles()).toHaveLength(4))
    const call = getCalls.filter((c) => c.url === '/cameras').at(-1)!
    expect(call.params).toMatchObject({ campus_id: 'k1', nvr_id: 'n9', q: 'gate', page_size: 500 })
  })

  it('typing in the search box filters through the API and the URL', async () => {
    renderWall()
    await waitFor(() => expect(tiles()).toHaveLength(4))
    await userEvent.type(screen.getByRole('textbox', { name: 'Search cameras' }), 'x')
    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('q=x'))
  })

  it('favorites mode asks only for favorite cameras and has no campus filters', async () => {
    renderWall('favorites')
    await waitFor(() => expect(tiles()).toHaveLength(4))
    expect(getCalls.filter((c) => c.url === '/cameras').at(-1)!.params).toMatchObject({ favorites: true })
    expect(screen.queryByRole('combobox', { name: 'Filter by campus' })).not.toBeInTheDocument()
  })

  it('shows a helpful empty state', async () => {
    cameras = []
    const { unmount } = renderWall()
    expect(await screen.findByText('No cameras to show')).toBeInTheDocument()
    unmount()
    renderWall('favorites')
    expect(await screen.findByText('No favorite cameras yet')).toBeInTheDocument()
  })

  it('shows an error with a retry instead of a blank screen', async () => {
    vi.spyOn(http, 'get').mockImplementation(async (url: string) => {
      if (url === '/cameras') throw new Error('server exploded')
      return { data: [] }
    })
    renderWall()
    expect(await screen.findByRole('alert')).toHaveTextContent('server exploded')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('opens a saved layout in its own camera order and tile count', async () => {
    layouts = [{ id: 'L1', name: 'Entrances', grid_size: 4, camera_ids: ['c3', 'c1', 'c2'], updated_at: '' }]
    renderWall('all', '/live?layout=L1')
    await waitFor(() => expect(tiles()).toEqual(['Cam 3', 'Cam 1', 'Cam 2']))
    expect(screen.getByText('Entrances')).toBeInTheDocument()
    const call = getCalls.filter((c) => c.url === '/cameras').at(-1)!
    expect(call.params).toMatchObject({ ids: ['c3', 'c1', 'c2'] })
    expect(screen.queryByRole('combobox', { name: 'Filter by campus' })).not.toBeInTheDocument() // layouts ignore filters
  })

  it('"Back to all cameras" leaves the layout', async () => {
    layouts = [{ id: 'L1', name: 'Entrances', grid_size: 4, camera_ids: ['c1'], updated_at: '' }]
    renderWall('all', '/live?layout=L1&grid=4')
    await userEvent.click(await screen.findByRole('button', { name: 'Back to all cameras' }))
    await waitFor(() => expect(screen.getByTestId('loc').textContent).not.toContain('layout='))
  })

  it('saves the cameras currently on screen as a layout', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({ data: {} })
    renderWall('all', '/live?grid=4&page=2')
    await waitFor(() => expect(tiles()).toEqual(['Cam 5', 'Cam 6', 'Cam 7', 'Cam 8']))
    await userEvent.click(screen.getByRole('button', { name: /Saved layouts/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Save current view/ }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Saves the 4 cameras on screen in a 4-tile layout/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save layout' })).toBeDisabled() // needs a name
    await userEvent.type(within(dialog).getByLabelText('Layout name'), ' Garage ')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save layout' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/layouts', { id: undefined, name: 'Garage', grid_size: 4, camera_ids: ['c5', 'c6', 'c7', 'c8'] }))
  })

  it('lists saved layouts in the menu and opens one', async () => {
    layouts = [{ id: 'L2', name: 'Parking', grid_size: 9, camera_ids: ['c1', 'c2'], updated_at: '' }]
    renderWall()
    await userEvent.click(screen.getByRole('button', { name: /Saved layouts/ }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Parking/ }))
    await waitFor(() => expect(screen.getByTestId('loc').textContent).toContain('layout=L2'))
    expect(screen.getByTestId('loc').textContent).toContain('grid=9')
  })

  it('pauses every tile while a camera is open fullscreen, and resumes afterwards', async () => {
    renderWall()
    await waitFor(() => expect(tiles()).toHaveLength(4))
    expect(screen.getAllByTestId('tile').every((t) => t.dataset.paused === 'false')).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'fs-Cam 2' }))
    expect(await screen.findByRole('dialog')).toHaveTextContent('fullscreen Cam 2')
    expect(screen.getAllByTestId('tile').every((t) => t.dataset.paused === 'true')).toBe(true)
  })
})
