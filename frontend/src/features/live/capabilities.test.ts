import { canPlayHevc, resetHevcCache } from './capabilities'

const original = { MediaSource: (globalThis as { MediaSource?: unknown }).MediaSource, canPlayType: HTMLMediaElement.prototype.canPlayType }

afterEach(() => {
  ;(globalThis as { MediaSource?: unknown }).MediaSource = original.MediaSource
  HTMLMediaElement.prototype.canPlayType = original.canPlayType
  resetHevcCache()
})

const withMse = (supported: boolean) => {
  ;(globalThis as { MediaSource?: unknown }).MediaSource = { isTypeSupported: vi.fn(() => supported) }
}

describe('canPlayHevc', () => {
  it('is true when Media Source Extensions can decode H.265', () => {
    withMse(true)
    expect(canPlayHevc()).toBe(true)
  })

  it('is true for browsers that play HLS/H.265 natively without MSE (Safari)', () => {
    ;(globalThis as { MediaSource?: unknown }).MediaSource = undefined
    HTMLMediaElement.prototype.canPlayType = () => 'probably'
    expect(canPlayHevc()).toBe(true)
  })

  it('is false when neither path can decode it, so the server converts to H.264 instead', () => {
    withMse(false)
    HTMLMediaElement.prototype.canPlayType = () => ''
    expect(canPlayHevc()).toBe(false)
  })

  it('treats a "maybe" from canPlayType as not good enough', () => {
    withMse(false)
    HTMLMediaElement.prototype.canPlayType = () => 'maybe'
    expect(canPlayHevc()).toBe(false)
  })

  it('never throws: an exploding browser API means "no"', () => {
    ;(globalThis as { MediaSource?: unknown }).MediaSource = {
      isTypeSupported: () => {
        throw new Error('boom')
      },
    }
    expect(canPlayHevc()).toBe(false)
  })

  it('asks the browser once and remembers', () => {
    withMse(true)
    canPlayHevc()
    canPlayHevc()
    expect((globalThis as unknown as { MediaSource: { isTypeSupported: ReturnType<typeof vi.fn> } }).MediaSource.isTypeSupported).toHaveBeenCalledTimes(1)
  })
})
