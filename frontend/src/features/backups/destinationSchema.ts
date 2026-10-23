import { z } from 'zod'

// Mirrors the backend rules for hosts and fingerprints.
const HOST_RE = /^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/
export const FINGERPRINT_RE = /^SHA256:[A-Za-z0-9+/]{43}$/

export function makeDestinationSchema(mode: 'create' | 'edit') {
  return z
    .object({
      name: z.string().trim().min(1, 'Name is required').max(128),
      host: z.string().trim().min(1, 'Address is required').regex(HOST_RE, 'Enter an IP address or hostname only (no sftp://, port or path)'),
      port: z.number({ error: 'Port must be a number' }).int('Port must be a whole number').min(1, 'Port must be between 1 and 65535').max(65535, 'Port must be between 1 and 65535'),
      username: z.string().trim().min(1, 'Username is required').max(128),
      auth_type: z.enum(['password', 'key']),
      password: z.string().max(256),
      private_key: z.string().max(16000),
      base_path: z.string().trim().min(1, 'Base folder is required').max(512).refine((v) => !v.split(/[\\/]/).includes('..'), 'The folder may not contain ".."'),
      enabled: z.boolean(),
    })
    .superRefine((v, ctx) => {
      if (mode === 'create' && v.auth_type === 'password' && !v.password)
        ctx.addIssue({ code: 'custom', path: ['password'], message: 'Password is required' })
      if (mode === 'create' && v.auth_type === 'key' && v.private_key.trim().length < 20)
        ctx.addIssue({ code: 'custom', path: ['private_key'], message: 'Paste the private key' })
    })
}

export type DestinationFormValues = z.infer<ReturnType<typeof makeDestinationSchema>>
