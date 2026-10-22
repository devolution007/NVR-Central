import { type RefObject, useEffect, useState } from 'react'

const HIDDEN_GRACE_MS = 30_000

/** True while a tile should hold a live session: it is on screen, and the tab has not been hidden for long.
 *  Off-screen tiles and background tabs release their streams so the NVR is not loaded for nobody. */
export function useWatchable(ref: RefObject<Element | null>): boolean {
  const [inView, setInView] = useState(true)
  const [tabActive, setTabActive] = useState(true)

  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([entry]) => setInView(entry?.isIntersecting ?? true), { rootMargin: '100px' })
    io.observe(el)
    return () => io.disconnect()
  }, [ref])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const onChange = () => {
      clearTimeout(timer)
      if (document.visibilityState === 'visible') setTabActive(true)
      else timer = setTimeout(() => setTabActive(false), HIDDEN_GRACE_MS) // brief tab switches keep the stream
    }
    document.addEventListener('visibilitychange', onChange)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onChange)
    }
  }, [])

  return inView && tabActive
}
