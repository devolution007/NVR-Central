import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ApiError, http } from '@/api/client'
import { useAuth } from '@/features/auth/store'
import { LoginPage } from './LoginPage'

function renderLogin() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>dashboard</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => useAuth.setState({ user: null, status: 'anonymous' }))
  afterEach(() => vi.restoreAllMocks())

  it('shows the server message when sign-in fails and stays on the page', async () => {
    vi.spyOn(http, 'post').mockRejectedValue(new ApiError(401, 'invalid_credentials', 'Invalid username or password'))
    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'admin')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password')
    expect(screen.queryByText('dashboard')).not.toBeInTheDocument()
  })

  it('signs in and moves to the dashboard on success', async () => {
    const user = { id: '1', username: 'admin', display_name: 'Admin', email: null, role: 'admin', is_active: true, last_login_at: null, created_at: '', campus_ids: [] }
    vi.spyOn(http, 'post').mockResolvedValue({ data: user })
    renderLogin()
    await userEvent.type(screen.getByLabelText('Username'), 'admin')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse-battery')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(screen.getByText('dashboard')).toBeInTheDocument())
    expect(useAuth.getState().status).toBe('authenticated')
  })
})
