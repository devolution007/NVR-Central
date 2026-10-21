import { render, screen } from '@testing-library/react'
import { CameraStatusIndicator, CapabilityIndicator, PeerStatusIndicator } from './StatusIndicators'

describe('status indicators never turn "unknown" into a verdict', () => {
  it('a camera that was never checked is "Not checked", not offline', () => {
    render(<CameraStatusIndicator status="unknown" />)
    expect(screen.getByText('Not checked')).toBeInTheDocument()
    expect(screen.queryByText('Not responding')).not.toBeInTheDocument()
  })

  it('distinguishes a camera that does not answer from one removed from the NVR', () => {
    render(
      <>
        <CameraStatusIndicator status="offline" />
        <CameraStatusIndicator status="unavailable" />
      </>,
    )
    expect(screen.getByText('Not responding')).toBeInTheDocument()
    expect(screen.getByText('Removed from NVR')).toBeInTheDocument()
  })

  it('a capability that was not tested says "Not verified", never "Not supported"', () => {
    render(<CapabilityIndicator status="unknown" />)
    expect(screen.getByText('Not verified')).toBeInTheDocument()
    expect(screen.queryByText('Not supported')).not.toBeInTheDocument()
  })

  it('only tested capabilities are called "Verified"', () => {
    render(
      <>
        <CapabilityIndicator status="supported_tested" />
        <CapabilityIndicator status="unsupported" />
      </>,
    )
    expect(screen.getByText('Verified')).toBeInTheDocument()
    expect(screen.getByText('Not supported')).toBeInTheDocument()
  })

  it('a tunnel with no health data is "Unknown", not down', () => {
    render(<PeerStatusIndicator status="unknown" />)
    expect(screen.getByText('Unknown')).toBeInTheDocument()
    expect(screen.getByTitle(/not the same as down/i)).toBeInTheDocument()
  })

  it.each([
    ['connected', 'Connected'],
    ['stale', 'No recent handshake'],
    ['never_connected', 'Never connected'],
    ['revoked', 'Revoked'],
  ] as const)('labels tunnel state %s', (status, label) => {
    render(<PeerStatusIndicator status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })
})
