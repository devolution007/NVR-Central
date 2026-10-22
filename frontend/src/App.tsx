import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useSessionBootstrap } from '@/features/auth/api'
import { AppLayout } from '@/layouts/AppLayout'
import { PLANNED_PAGES } from '@/layouts/nav'
import { CampusesPage } from '@/pages/CampusesPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { LoginPage } from '@/pages/LoginPage'
import { NvrsPage } from '@/pages/NvrsPage'
import { NotFoundPage, PlannedPage } from '@/pages/PlaceholderPages'
import { CamerasPage } from '@/pages/CamerasPage'
import { ConnectivityPage } from '@/pages/ConnectivityPage'
import { NvrDetailPage } from '@/pages/NvrDetailPage'
import { ProtectedRoute } from '@/routes/ProtectedRoute'

// Live pages pull in the video library, so they load on demand and keep the first page load small.
const LiveAllPage = lazy(() => import('@/pages/LivePages').then((m) => ({ default: m.LiveAllPage })))
const LiveFavoritesPage = lazy(() => import('@/pages/LivePages').then((m) => ({ default: m.LiveFavoritesPage })))
const LiveLayoutsPage = lazy(() => import('@/pages/LivePages').then((m) => ({ default: m.LiveLayoutsPage })))
const lazyPage = (el: React.ReactNode) => <Suspense fallback={<p className="text-sm text-muted-foreground" role="status">Loading…</p>}>{el}</Suspense>

export default function App() {
  useSessionBootstrap()
  return (
    <TooltipProvider delayDuration={200}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="campuses" element={<CampusesPage />} />
            <Route path="nvrs" element={<NvrsPage />} />
            <Route path="nvrs/:id" element={<NvrDetailPage />} />
            <Route path="connectivity" element={<ConnectivityPage />} />
            <Route path="cameras" element={<CamerasPage />} />
            <Route path="live" element={lazyPage(<LiveAllPage />)} />
            <Route path="live/favorites" element={lazyPage(<LiveFavoritesPage />)} />
            <Route path="live/layouts" element={lazyPage(<LiveLayoutsPage />)} />
            {PLANNED_PAGES.map((p) => (
              <Route key={p.to} path={p.to.slice(1)} element={<PlannedPage />} />
            ))}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
      <Toaster richColors closeButton />
    </TooltipProvider>
  )
}
