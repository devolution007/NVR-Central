import { z } from 'zod'

// Mirrors the backend rule: an IP address or hostname, with no scheme, port or path.
const HOST_RE = /^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/

const port = (label: string) =>
  z
    .number({ error: `${label} must be a number` })
    .int(`${label} must be a whole number`)
    .min(1, `${label} must be between 1 and 65535`)
    .max(65535, `${label} must be between 1 and 65535`)

export function makeNvrSchema(mode: 'create' | 'edit') {
  return z
    .object({
      campus_id: z.string().min(1, 'Choose a campus'),
      name: z.string().trim().min(1, 'Name is required').max(128),
      connection_type: z.enum(['tunnel', 'lan', 'p2p']),
      host: z.string().trim().max(253),
      http_port: port('HTTP port'),
      rtsp_port: port('RTSP port'),
      username: z.string().min(1, 'Username is required').max(64),
      password: z.string().max(128),
      p2p_serial: z.string().trim().max(64),
      enabled: z.boolean(),
    })
    .superRefine((v, ctx) => {
      if (v.connection_type !== 'p2p') {
        if (!v.host) ctx.addIssue({ code: 'custom', path: ['host'], message: 'Address is required for tunnel and LAN connections' })
        else if (!HOST_RE.test(v.host))
          ctx.addIssue({ code: 'custom', path: ['host'], message: 'Enter an IP address or hostname only (no http://, port or path)' })
      }
      if (v.connection_type === 'p2p' && mode === 'create' && v.p2p_serial.length < 4)
        ctx.addIssue({ code: 'custom', path: ['p2p_serial'], message: 'Serial number is required for P2P connections' })
      if (mode === 'create' && !v.password)
        ctx.addIssue({ code: 'custom', path: ['password'], message: 'Password is required' })
    })
}

export type NvrFormValues = z.infer<ReturnType<typeof makeNvrSchema>>

export const campusSchema = z.object({
  name: z.string().trim().min(1, 'Name is required').max(128),
  description: z.string().max(2000),
  location: z.string().max(256),
})
export type CampusFormValues = z.infer<typeof campusSchema>
