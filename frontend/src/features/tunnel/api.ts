import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { http } from '@/api/client'
import type { PeerCreated, PeerInput, TunnelHub, TunnelPeer } from '@/api/types'

const KEY = ['tunnel'] as const

export const useHub = () =>
  useQuery({ queryKey: [...KEY, 'hub'], queryFn: async () => (await http.get<TunnelHub>('/tunnel/hub')).data })

export const usePeers = () =>
  useQuery({ queryKey: [...KEY, 'peers'], queryFn: async () => (await http.get<TunnelPeer[]>('/tunnel/peers')).data })

export function useCreatePeer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: PeerInput) => (await http.post<PeerCreated>('/tunnel/peers', v)).data,
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useRevokePeer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await http.delete(`/tunnel/peers/${id}`)
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: KEY }),
  })
}

export async function fetchHubConfig(): Promise<string> {
  return (await http.get<string>('/tunnel/hub-config', { responseType: 'text' })).data
}
