import { create } from 'zustand'

export type Theme = 'dark' | 'light'
const KEY = 'nvr-central-theme'

function loadTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark' // storage can be blocked (private windows); the app must still work
  }
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('light', theme === 'light')
}

interface UiState {
  theme: Theme
  sidebarOpen: boolean
  toggleTheme: () => void
  setSidebarOpen: (open: boolean) => void
}

export const useUi = create<UiState>((set, get) => ({
  theme: loadTheme(),
  sidebarOpen: false,
  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    try {
      localStorage.setItem(KEY, next)
    } catch {
      /* ignore */
    }
    applyTheme(next)
    set({ theme: next })
  },
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
}))

applyTheme(useUi.getState().theme)
