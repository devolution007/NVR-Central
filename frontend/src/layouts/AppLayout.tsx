import { LogOut, Menu, Moon, Sun, Video, X } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useLogout } from '@/features/auth/api'
import { isAdmin, useAuth } from '@/features/auth/store'
import { cn } from '@/lib/utils'
import { useUi } from '@/stores/ui'
import { NAV } from './nav'

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const user = useAuth((s) => s.user)
  return (
    <nav aria-label="Main" className="flex-1 space-y-4 overflow-y-auto px-3 py-4">
      {NAV.map((group, i) => {
        const items = group.items.filter((it) => !it.adminOnly || isAdmin(user))
        if (items.length === 0) return null
        return (
          <div key={group.label ?? i}>
            {group.label && (
              <p className="mb-1 px-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">{group.label}</p>
            )}
            <ul className="space-y-0.5">
              {items.map((it) => (
                <li key={it.to}>
                  <NavLink
                    to={it.to}
                    end={it.to === '/'}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors',
                        isActive ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                      )
                    }
                  >
                    <it.icon className="size-4 shrink-0" aria-hidden />
                    <span className="truncate">{it.label}</span>
                    {it.phase !== undefined && (
                      <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground" title={`Built in Phase ${it.phase}`}>
                        P{it.phase}
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </nav>
  )
}

function Brand() {
  return (
    <div className="flex h-14 items-center gap-2 border-b px-4">
      <div className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <Video className="size-4" aria-hidden />
      </div>
      <span className="font-semibold tracking-tight">NVR Central</span>
    </div>
  )
}

export function AppLayout() {
  const user = useAuth((s) => s.user)
  const { theme, toggleTheme, sidebarOpen, setSidebarOpen } = useUi()
  const logout = useLogout()
  const { pathname } = useLocation()

  useEffect(() => setSidebarOpen(false), [pathname])

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-sidebar lg:flex">
        <Brand />
        <SidebarNav />
      </aside>

      {sidebarOpen && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button className="absolute inset-0 bg-black/60" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r bg-sidebar">
            <div className="flex items-center justify-between">
              <Brand />
              <Button variant="ghost" size="icon" className="mr-2" onClick={() => setSidebarOpen(false)} aria-label="Close navigation">
                <X />
              </Button>
            </div>
            <SidebarNav onNavigate={() => setSidebarOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/90 px-4 backdrop-blur">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setSidebarOpen(true)} aria-label="Open navigation">
            <Menu />
          </Button>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
              {theme === 'dark' ? <Sun /> : <Moon />}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" className="gap-2">
                  <span className="flex size-7 items-center justify-center rounded-full bg-secondary text-xs font-semibold uppercase">
                    {(user?.display_name || user?.username || '?').slice(0, 1)}
                  </span>
                  <span className="hidden text-sm sm:inline">{user?.display_name || user?.username}</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>
                  <div className="text-sm">{user?.username}</div>
                  <div className="text-xs font-normal capitalize text-muted-foreground">{user?.role}</div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => logout.mutate()}>
                  <LogOut /> Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
