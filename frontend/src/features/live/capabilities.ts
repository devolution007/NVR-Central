let cached: boolean | undefined

/** Can this browser decode H.265 itself? If so the server passes the camera's video through untouched instead of
 *  converting it to H.264, which saves most of the server CPU. Checked once per page load. */
export function canPlayHevc(): boolean {
  if (cached !== undefined) return cached
  const type = 'video/mp4; codecs="hvc1.1.6.L93.B0"'
  try {
    const mse = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(type)
    const native = typeof document !== 'undefined' && document.createElement('video').canPlayType(type) === 'probably'
    cached = mse || native
  } catch {
    cached = false
  }
  return cached
}

/** Test helper: forget the cached answer. */
export function resetHevcCache(): void {
  cached = undefined
}
