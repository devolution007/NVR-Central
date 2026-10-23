/** datetime-local input value for a Date, in the browser's timezone. */
export function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** A datetime-local value (browser timezone) to the UTC ISO string the API expects. */
export function localInputToIso(value: string): string {
  return new Date(value).toISOString()
}
