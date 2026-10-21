import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

export interface FieldIssue {
  field: string
  message: string
}

/** Normalised error from the backend's {"error": {code, message, details}} envelope. */
export class ApiError extends Error {
  status: number
  code: string
  details: FieldIssue[]

  constructor(status: number, code: string, message: string, details: FieldIssue[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export const http = axios.create({ baseURL: '/api/v1', withCredentials: true })

const SAFE_METHODS = new Set(['get', 'head', 'options'])

function readCookie(name: string): string | undefined {
  const hit = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : undefined
}

http.interceptors.request.use((config) => {
  if (!SAFE_METHODS.has((config.method ?? 'get').toLowerCase())) {
    const token = readCookie('csrf_token')
    if (token) config.headers.set('X-CSRF-Token', token)
  }
  return config
})

let onSessionExpired: () => void = () => {}
export function setSessionExpiredHandler(fn: () => void) {
  onSessionExpired = fn
}

// One refresh at a time; concurrent 401s wait for the same attempt.
let refreshing: Promise<void> | null = null

function refreshSession(): Promise<void> {
  refreshing ??= http
    .post('/auth/refresh')
    .then(() => undefined)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

type Retryable = InternalAxiosRequestConfig & { _retried?: boolean }

http.interceptors.response.use(
  (r) => r,
  async (error: AxiosError<{ error?: { code: string; message: string; details?: FieldIssue[] } }>) => {
    const config = error.config as Retryable | undefined
    const url = config?.url ?? ''
    const isAuthCall = url.startsWith('/auth/login') || url.startsWith('/auth/refresh') || url.startsWith('/auth/logout')

    // No csrf_token cookie means there is no session to refresh (never signed in, or signed out).
    const hasSession = readCookie('csrf_token') !== undefined
    if (error.response?.status === 401 && config && !config._retried && !isAuthCall && hasSession) {
      config._retried = true
      try {
        await refreshSession()
        return http(config)
      } catch {
        onSessionExpired()
      }
    }

    const body = error.response?.data?.error
    throw new ApiError(
      error.response?.status ?? 0,
      body?.code ?? (error.response ? 'http_error' : 'network_error'),
      body?.message ?? (error.response ? `Request failed (${error.response.status})` : 'Cannot reach the server'),
      body?.details ?? [],
    )
  },
)

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong'
}
