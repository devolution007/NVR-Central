import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { http } from '@/api/client'
import type { Layout, LayoutInput, StreamKind, StreamSession } from '@/api/types'
import { canPlayHevc } from './capabilities'

export const startStream = async (cameraId: string, stream: StreamKind): Promise<StreamSession> =>
  (await http.post<StreamSession>(`/cameras/${cameraId}/stream-session`, { stream, native_hevc: canPlayHevc() })).data

export const keepaliveStream = async (sessionId: string): Promise<void> => {
  await http.post(`/stream-sessions/${sessionId}/keepalive`)
}

export const stopStream = async (sessionId: string): Promise<void> => {
  await http.delete(`/stream-sessions/${sessionId}`)
}

/** Tab or window closing: the request must outlive the page, which axios cannot guarantee. */
export function stopStreamOnUnload(sessionId: string): void {
  const csrf = document.cookie.split('; ').find((c) => c.startsWith('csrf_token='))?.split('=')[1]
  void fetch(`/api/v1/stream-sessions/${sessionId}`, {
    method: 'DELETE',
    keepalive: true,
    credentials: 'include',
    headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {},
  }).catch(() => undefined)
}

export function useToggleFavorite() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ cameraId, favorite }: { cameraId: string; favorite: boolean }) => {
      if (favorite) await http.put(`/cameras/${cameraId}/favorite`)
      else await http.delete(`/cameras/${cameraId}/favorite`)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['cameras'] }),
  })
}

export const layoutKeys = ['layouts'] as const

export const useLayouts = () =>
  useQuery({ queryKey: layoutKeys, queryFn: async () => (await http.get<Layout[]>('/layouts')).data })

export function useSaveLayout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: LayoutInput & { id?: string }) =>
      v.id ? (await http.put<Layout>(`/layouts/${v.id}`, v)).data : (await http.post<Layout>('/layouts', v)).data,
    onSuccess: () => void qc.invalidateQueries({ queryKey: layoutKeys }),
  })
}

export function useDeleteLayout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await http.delete(`/layouts/${id}`)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: layoutKeys }),
  })
}
