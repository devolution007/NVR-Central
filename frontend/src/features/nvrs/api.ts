import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { http } from '@/api/client'
import type { DashboardSummary, Nvr, NvrInput, NvrStatus, Page } from '@/api/types'

export const nvrKeys = { all: ['nvrs'] as const }

export interface NvrListParams {
  campus_id?: string
  status?: NvrStatus
  q?: string
  page?: number
  page_size?: number
}

export function useNvrs(params: NvrListParams = {}) {
  return useQuery({
    queryKey: [...nvrKeys.all, params],
    queryFn: async () => (await http.get<Page<Nvr>>('/nvrs', { params })).data,
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
