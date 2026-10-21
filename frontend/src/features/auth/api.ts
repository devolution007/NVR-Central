import { useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, http, setSessionExpiredHandler } from '@/api/client'
import type { User } from '@/api/types'
import { useAuth } from './store'

export async function fetchMe(): Promise<User | null> {
  try {
    const { data } = await http.get<User>('/auth/me')
    return data
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null
    throw err
  }
}

/** Resolves the session once at startup and reacts when a refresh fails mid-session. */
export function useSessionBootstrap() {
  const setUser = useAuth((s) => s.setUser)
  const qc = useQueryClient()

  useEffect(() => {
    setSessionExpiredHandler(() => {
      qc.clear()
      setUser(null)
    })
    let cancelled = false
    fetchMe()
      .then((u) => !cancelled && setUser(u))
      .catch(() => !cancelled && setUser(null))
    return () => {
      cancelled = true
    }
  }, [qc, setUser])
}

export function useLogin() {
  const setUser = useAuth((s) => s.setUser)
  return useMutation({
    mutationFn: async (v: { username: string; password: string }) => (await http.post<User>('/auth/login', v)).data,
    onSuccess: (user) => setUser(user),
  })
}

export function useLogout() {
  const setUser = useAuth((s) => s.setUser)
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      await http.post('/auth/logout')
    },
    onSettled: () => {
      qc.clear()
      setUser(null)
    },
  })
}
