import { create } from 'zustand'
import type { User } from '@/api/types'

type Status = 'loading' | 'authenticated' | 'anonymous'

interface AuthState {
  user: User | null
  status: Status
  setUser: (user: User | null) => void
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  status: 'loading',
  setUser: (user) => set({ user, status: user ? 'authenticated' : 'anonymous' }),
}))

export const isAdmin = (user: User | null) => user?.role === 'admin'
