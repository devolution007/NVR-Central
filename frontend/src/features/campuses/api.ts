import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { http } from '@/api/client'
import type { Campus, CampusInput, Page } from '@/api/types'

export const campusKeys = { all: ['campuses'] as const }

export function useCampuses(params: { q?: string; page?: number; page_size?: number } = {}) {
  return useQuery({
    queryKey: [...campusKeys.all, params],
    queryFn: async () => (await http.get<Page<Campus>>('/campuses', { params })).data,
    placeholderData: keepPreviousData,
  })
}

function useInvalidating<TVars, TData>(fn: (v: TVars) => Promise<TData>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: campusKeys.all })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
      void qc.invalidateQueries({ queryKey: ['nvrs'] })
    },
  })
}

export const useCreateCampus = () =>
  useInvalidating(async (v: CampusInput) => (await http.post<Campus>('/campuses', v)).data)

export const useUpdateCampus = () =>
  useInvalidating(async ({ id, ...v }: CampusInput & { id: string }) => (await http.put<Campus>(`/campuses/${id}`, v)).data)

export const useDeleteCampus = () =>
  useInvalidating(async (id: string) => {
    await http.delete(`/campuses/${id}`)
  })
