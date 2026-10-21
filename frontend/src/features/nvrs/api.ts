import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { http } from '@/api/client'
import type { Camera, CameraStatus, Capability, DashboardSummary, Nvr, NvrInput, NvrStatus, Page } from '@/api/types'

export const nvrKeys = { all: ['nvrs'] as const }

export interface NvrListParams {
  campus_id?: string
  status?: NvrStatus
  q?: string
  page?: number
  page_size?: number
}

/** While any NVR is mid-check the list refreshes every 2 s so the user sees the result without reloading. */
const pollWhileConnecting = (items: Nvr[] | undefined) => (items?.some((n) => n.status === 'connecting') ? 2000 : false)

export function useNvrs(params: NvrListParams = {}) {
  return useQuery({
    queryKey: [...nvrKeys.all, 'list', params],
    queryFn: async () => (await http.get<Page<Nvr>>('/nvrs', { params })).data,
    placeholderData: keepPreviousData,
    refetchInterval: (q) => pollWhileConnecting(q.state.data?.items),
  })
}

export function useNvr(id: string) {
  return useQuery({
    queryKey: [...nvrKeys.all, 'detail', id],
    queryFn: async () => (await http.get<Nvr>(`/nvrs/${id}`)).data,
    refetchInterval: (q) => (q.state.data?.status === 'connecting' ? 2000 : false),
  })
}

export function useNvrCapabilities(id: string, status?: NvrStatus) {
  return useQuery({
    queryKey: [...nvrKeys.all, 'capabilities', id, status],
    queryFn: async () => (await http.get<Capability[]>(`/nvrs/${id}/capabilities`)).data,
  })
}

export function useNvrCameras(id: string, status?: NvrStatus) {
  return useQuery({
    queryKey: [...nvrKeys.all, 'cameras', id, status],
    queryFn: async () => (await http.get<Camera[]>(`/nvrs/${id}/cameras`)).data,
  })
}

export interface CameraListParams {
  campus_id?: string
  nvr_id?: string
  status?: CameraStatus
  q?: string
  page?: number
  page_size?: number
}

export function useCameras(params: CameraListParams = {}) {
  return useQuery({
    queryKey: ['cameras', params],
    queryFn: async () => (await http.get<Page<Camera>>('/cameras', { params })).data,
    placeholderData: keepPreviousData,
  })
}

export function useDashboardSummary() {
  return useQuery({
    queryKey: ['dashboard', 'summary'],
    queryFn: async () => (await http.get<DashboardSummary>('/dashboard/summary')).data,
  })
}

function useInvalidating<TVars, TData>(fn: (v: TVars) => Promise<TData>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: nvrKeys.all })
      void qc.invalidateQueries({ queryKey: ['campuses'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
      void qc.invalidateQueries({ queryKey: ['cameras'] })
    },
  })
}

export const useCreateNvr = () => useInvalidating(async (v: NvrInput) => (await http.post<Nvr>('/nvrs', v)).data)

export const useUpdateNvr = () =>
  useInvalidating(async ({ id, ...v }: NvrInput & { id: string }) => (await http.put<Nvr>(`/nvrs/${id}`, v)).data)

export const useDeleteNvr = () =>
  useInvalidating(async (id: string) => {
    await http.delete(`/nvrs/${id}`)
  })

export type NvrAction = 'test' | 'discover' | 'reconnect'

/** `force` retries an NVR whose credentials were rejected earlier (risks locking the NVR account). */
export const useNvrAction = () =>
  useInvalidating(async ({ id, action, force }: { id: string; action: NvrAction; force?: boolean }) => {
    await http.post(`/nvrs/${id}/${action}`, null, { params: force ? { force: true } : undefined })
  })
