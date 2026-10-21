# Architecture notes (Phase 1)

## Request flow

Browser -> Vite dev server (or the reverse proxy later) -> `/api/v1` FastAPI -> PostgreSQL. The browser never receives NVR credentials, serials or tokens it can read: access and refresh tokens are HttpOnly cookies, NVR secrets are write-only.

## Data model (migration `624eb3e1bc4d`)

`users`, `user_campus_permissions`, `refresh_sessions`, `campuses`, `nvrs`, `cameras`, `camera_backup_policies`, `audit_logs`. UUID keys, timezone-aware UTC timestamps, soft delete on users/campuses/NVRs, case-insensitive partial unique indexes on active names.

Tables from the spec that arrive in later phases: `nvr_capabilities`, `nvr_connections`, `camera_stream_profiles`, `camera_favorites`, `saved_camera_layouts`, `sftp_destinations`, `recordings`, `backup_jobs`, `backup_job_items`, `backup_files`, `backup_checkpoints`, `stream_sessions`, `notifications`.

## Decisions and deviations from the spec

| Spec | What we did | Why |
|---|---|---|
| `roles` table | `users.role` column (admin/operator/viewer) | Three fixed roles; a table adds joins with no benefit. Easy to migrate if custom roles are needed |
| NVR registered by P2P serial | NVR has a **connection profile**: `tunnel`, `lan` or `p2p` | Direct P2P failed hardware testing (docs/p2p-compatibility.md). Serial is optional and only for P2P |
| Access via secure cookies | Cookies plus an `Authorization: Bearer` path for tooling | Bearer requests skip CSRF (no ambient credentials) |
| Redis rate limiting | In-process limiter plus DB account lockout | Redis arrives with Celery in Phase 2. The lockout is already multi-worker safe |
| Camera backup stream | `camera_backup_policies.backup_stream` = `sub` (default) or `main` | Measured: main is about 908 MB/hour/camera, sub about 232 MB. Decision 2026-10-21 |
| Camera channel numbering | `cameras.channel_number` is **1-based** (Dahua CGI search and RTSP) | `ChannelTitle[]` in the NVR config is 0-based, so convert at the adapter boundary |
| NVR local time | `nvrs.timezone_code` holds Dahua's `NTP.TimeZone` enum | Needed to convert NVR timestamps to UTC (test NVR reports code 7, Islamabad) |

## Auth details

- Access JWT (15 min) in `access_token`; refresh token (7 days, opaque, rotated on every use) in `refresh_token` scoped to `/api/v1/auth`; `csrf_token` readable by the SPA and echoed in `X-CSRF-Token`.
- Every request re-reads the user, so disabling an account or changing its role applies immediately, not at token expiry.
- Login errors are identical for unknown user and wrong password, and a dummy hash is verified for unknown users to equalise timing.
- Campus visibility: admins see all. Others see only assigned campuses; resources outside them return 404, not 403.

## Known limitations

- Behind a reverse proxy the audit log and login limiter see the proxy IP. Phase 5 adds trusted forwarded headers.
- Nothing contacts an NVR yet. NVRs stay `pending` ("Not tested").
- The user-management UI is Phase 5; the API exists (`/api/v1/users`).
- The frontend bundle is one 737 kB chunk; route-level code splitting comes with the live-view work.
