import { Construction } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { EmptyState, PageHeader } from '@/components/PageHeader'
import { PLANNED_PAGES } from '@/layouts/nav'

const PHASE_NOTE: Record<number, string> = {
  3: 'Live monitoring arrives in Phase 3, after camera discovery.',
  4: 'Recording search, SFTP and automatic backups arrive in Phase 4.',
  5: 'User management UI, notifications and settings arrive in Phase 5. The user and audit-log APIs already exist.',
}

/** Honest placeholder: says what the page will do and when, never shows fake data. */
export function PlannedPage() {
  const { pathname } = useLocation()
  const item = PLANNED_PAGES.find((p) => p.to === pathname)
  return (
    <>
      <PageHeader title={item?.label ?? 'Not available yet'} />
      <EmptyState icon={<Construction className="size-8" />} title={`Planned for Phase ${item?.phase ?? '?'}`}>
        {PHASE_NOTE[item?.phase ?? 0] ?? 'This page is not built yet.'}
      </EmptyState>
    </>
  )
}

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <EmptyState title="That page does not exist">
        <Link to="/" className="font-medium text-primary underline underline-offset-4">
          Back to the dashboard
        </Link>
      </EmptyState>
    </>
  )
}
