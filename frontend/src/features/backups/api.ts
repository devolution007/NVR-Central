import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { http } from '@/api/client'
import type {
  BackupJob,
  BackupJobDetail,
  BackupJobStatus,
  BackupPolicy,
  BackupStatus,
  DestinationInput,
  DestinationOption,
  HostKey,
  JobCreateInput,
  Page,
  PolicyInput,
  RecordingSearch,
  SftpDestination,
} from '@/api/types'

export const backupKeys = {
  destinations: ['backups', 'destinations'] as const,
  options: ['backups', 'destination-options'] as const,
  policies: ['backups', 'policies'] as const,
  jobs: ['backups', 'jobs'] as const,
  recordings: ['backups', 'recordings'] as const,
  status: ['backups', 'status'] as const,
}

export const isActiveJob = (s: BackupJobStatus) => s === 'queued' || s === 'running'

/** Live progress without a socket: while anything is queued or running the data refreshes every 2 s. */
const POLL_MS = 2000

// ------------------------------------------------------------------------------------------------ destinations
export function useDestinations(enabled = true) {
  return useQuery({
    queryKey: backupKeys.destinations,
    queryFn: async () => (await http.get<SftpDestination[]>('/backups/destinations')).data,
    enabled,
  })
}

/** Names only, for operators choosing where a backup goes. */
export function useDestinationOptions() {
  return useQuery({
    queryKey: backupKeys.options,
    queryFn: async () => (await http.get<DestinationOption[]>('/backups/destinations/options')).data,
  })
}

export const probeHostKey = async (host: string, port: number) =>
  (await http.post<HostKey>('/backups/destinations/host-key', { host, port })).data

function useBackupMutation<TVars, TData>(fn: (v: TVars) => Promise<TData>, keys: readonly (readonly string[])[] = [backupKeys.destinations, backupKeys.options]) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const k of keys) void qc.invalidateQueries({ queryKey: k })
      void qc.invalidateQueries({ queryKey: backupKeys.status })
    },
  })
}

export const useCreateDestination = () =>
  useBackupMutation(async (v: DestinationInput) => (await http.post<SftpDestination>('/backups/destinations', v)).data)
export const useUpdateDestination = () =>
  useBackupMutation(async ({ id, ...v }: DestinationInput & { id: string }) => (await http.put<SftpDestination>(`/backups/destinations/${id}`, v)).data)
export const useDeleteDestination = () => useBackupMutation(async (id: string) => void (await http.delete(`/backups/destinations/${id}`)))
export const useTestDestination = () =>
  useBackupMutation(async (id: string) => (await http.post<SftpDestination>(`/backups/destinations/${id}/test`)).data)

// ---------------------------------------------------------------------------------------------------- policies
export interface PolicyListParams {
  campus_id?: string
  nvr_id?: string
  enabled?: boolean
  page?: number
  page_size?: number
}

export function usePolicies(params: PolicyListParams = {}) {
  return useQuery({
    queryKey: [...backupKeys.policies, params],
    queryFn: async () => (await http.get<Page<BackupPolicy>>('/backups/policies', { params })).data,
    placeholderData: keepPreviousData,
  })
}

export const useSetPolicy = () =>
  useBackupMutation(
    async ({ cameraId, ...v }: PolicyInput & { cameraId: string }) => (await http.put<BackupPolicy>(`/backups/policies/${cameraId}`, v)).data,
    [backupKeys.policies],
  )
export const useBulkPolicy = () =>
  useBackupMutation(async (v: PolicyInput & { camera_ids: string[] }) => (await http.post<BackupPolicy[]>('/backups/policies/bulk', v)).data, [
    backupKeys.policies,
  ])

// -------------------------------------------------------------------------------------------------- recordings
export function useRecordings(cameraId: string | undefined, start: string, end: string, destinationId?: string) {
  return useQuery({
    queryKey: [...backupKeys.recordings, cameraId, start, end, destinationId],
    queryFn: async () =>
      (await http.get<RecordingSearch>(`/backups/cameras/${cameraId}/recordings`, { params: { start, end, destination_id: destinationId } })).data,
    enabled: !!cameraId,
    retry: false,
    refetchOnWindowFocus: false,
  })
}

// --------------------------------------------------------------------------------------------------------- jobs
export interface JobListParams {
  status?: BackupJobStatus
  active?: boolean
  campus_id?: string
  camera_id?: string
  trigger?: 'scheduled' | 'manual'
  page?: number
  page_size?: number
}

export function useJobs(params: JobListParams = {}) {
  return useQuery({
    queryKey: [...backupKeys.jobs, params],
    queryFn: async () => (await http.get<Page<BackupJob>>('/backups/jobs', { params })).data,
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (params.active || q.state.data?.items.some((j) => isActiveJob(j.status)) ? POLL_MS : 15000),
  })
}

export function useJob(id: string | undefined) {
  return useQuery({
    queryKey: [...backupKeys.jobs, 'detail', id],
    queryFn: async () => (await http.get<BackupJobDetail>(`/backups/jobs/${id}`)).data,
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && isActiveJob(q.state.data.status) ? POLL_MS : false),
  })
}

export const useCreateJob = () =>
  useBackupMutation(async (v: JobCreateInput) => (await http.post<BackupJob>('/backups/jobs', v)).data, [backupKeys.jobs, backupKeys.recordings])
export const useCancelJob = () => useBackupMutation(async (id: string) => (await http.post<BackupJob>(`/backups/jobs/${id}/cancel`)).data, [backupKeys.jobs])
export const useRetryJob = () => useBackupMutation(async (id: string) => (await http.post<BackupJob>(`/backups/jobs/${id}/retry`)).data, [backupKeys.jobs])

export function useBackupStatus() {
  return useQuery({
    queryKey: backupKeys.status,
    queryFn: async () => (await http.get<BackupStatus>('/backups/status')).data,
    refetchInterval: 15000,
  })
}
