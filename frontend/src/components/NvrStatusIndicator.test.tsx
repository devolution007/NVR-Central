import { render, screen } from '@testing-library/react'
import { NvrStatusIndicator } from './NvrStatusIndicator'

describe('NvrStatusIndicator', () => {
  it('shows an untested NVR as "Not tested", never as offline', () => {
    render(<NvrStatusIndicator status="pending" />)
    expect(screen.getByText('Not tested')).toBeInTheDocument()
    expect(screen.queryByText('Offline')).not.toBeInTheDocument()
  })

  it.each([
    ['connected', 'Connected'],
    ['offline', 'Offline'],
    ['auth_failed', 'Authentication failed'],
    ['p2p_unavailable', 'P2P unavailable'],
    ['partially_supported', 'Partially supported'],
    ['unsupported', 'Unsupported'],
    ['connecting', 'Connecting'],
  ] as const)('labels %s with text (colour is never the only signal)', (status, label) => {
    render(<NvrStatusIndicator status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('exposes the server status message as a tooltip', () => {
    render(<NvrStatusIndicator status="auth_failed" message="Wrong password" />)
    expect(screen.getByTitle('Wrong password')).toBeInTheDocument()
  })
})
