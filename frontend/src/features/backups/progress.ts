export function progressPercent(done: number, total: number): number {
  if (!total) return 0
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)))
}
