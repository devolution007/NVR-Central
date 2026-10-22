import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

// The first render of a routed page can take seconds on a busy machine (many modules to load).
configure({ asyncUtilTimeout: 10_000 })

// jsdom lacks a few browser APIs that Radix UI (tooltips, dialogs, selects) relies on.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver
Element.prototype.scrollIntoView ??= () => {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}

// jsdom has no media pipeline: the video element methods the live player calls do nothing here.
HTMLMediaElement.prototype.load = () => {}
HTMLMediaElement.prototype.play = () => Promise.resolve()
HTMLMediaElement.prototype.pause = () => {}
