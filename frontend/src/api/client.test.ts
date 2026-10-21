import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import { ApiError, http } from './client'

function useAdapter(handler: (cfg: InternalAxiosRequestConfig) => { status: number; data?: unknown }) {
  const calls: InternalAxiosRequestConfig[] = []
  const adapter: AxiosAdapter = (cfg) => {
    calls.push(cfg)
    const { status, data } = handler(cfg)
    const response = { data, status, statusText: '', headers: {}, config: cfg }
    return status >= 200 && status < 300
      ? Promise.resolve(response)
      : Promise.reject(Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, config: cfg, response }))
  }
  http.defaults.adapter = adapter
  return calls
}

describe('http client', () => {
  beforeEach(() => {
    document.cookie = 'csrf_token=abc123; path=/'
  })

  it('sends the CSRF token on writes but not on reads', async () => {
    const calls = useAdapter(() => ({ status: 200, data: {} }))
    await http.get('/campuses')
    await http.post('/campuses', { name: 'x' })
    await http.delete('/campuses/1')
    expect(calls[0]!.headers.get('X-CSRF-Token')).toBeFalsy()
    expect(calls[1]!.headers.get('X-CSRF-Token')).toBe('abc123')
    expect(calls[2]!.headers.get('X-CSRF-Token')).toBe('abc123')
  })

  it('turns the backend error envelope into ApiError with field details', async () => {
    useAdapter(() => ({
      status: 422,
      data: { error: { code: 'validation_error', message: 'Request validation failed', details: [{ field: 'host', message: 'bad' }] } },
    }))
    const err = await http.post('/nvrs', {}).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 422, code: 'validation_error', details: [{ field: 'host', message: 'bad' }] })
  })

  it('refreshes the session once on 401 and retries the original request', async () => {
    let first = true
    const calls = useAdapter((cfg) => {
      if (cfg.url === '/auth/refresh') return { status: 200, data: {} }
      if (first) {
        first = false
        return { status: 401, data: { error: { code: 'invalid_token', message: 'expired' } } }
      }
      return { status: 200, data: { ok: true } }
    })
    const r = await http.get('/campuses')
    expect(r.data).toEqual({ ok: true })
    expect(calls.map((c) => c.url)).toEqual(['/campuses', '/auth/refresh', '/campuses'])
  })

  it('does not attempt a refresh when there is no session cookie at all', async () => {
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
    const calls = useAdapter(() => ({ status: 401, data: { error: { code: 'not_authenticated', message: 'Authentication required' } } }))
    await expect(http.get('/auth/me')).rejects.toMatchObject({ status: 401 })
    expect(calls.map((c) => c.url)).toEqual(['/auth/me'])
  })

  it('does not try to refresh when the login call itself returns 401', async () => {
    const calls = useAdapter(() => ({ status: 401, data: { error: { code: 'invalid_credentials', message: 'Invalid username or password' } } }))
    await expect(http.post('/auth/login', {})).rejects.toMatchObject({ code: 'invalid_credentials' })
    expect(calls).toHaveLength(1)
  })
})
