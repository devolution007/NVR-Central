# NVR Central — Phased Delivery Plan

Source spec: `Master Prompt for NVR Centeral App.txt`. Each phase ends with a working, demoable result and an exit gate. We do not start the next phase until the gate is passed.

Rule from the spec: nothing hardware-dependent is marked "operational" until it passes a real test against the DHI-NVR2116HS-4KS3.

## Environment (checked 2026-10-21)
- Docker 29.8 present, Node 24.18 present, Python 3.14.6 present.
- Project folder was empty (no git repo yet).
- Spec requires Python 3.12+. Use a 3.12 or 3.13 venv/Docker image for the backend, because 3.14 may lack wheels for some dependencies (psycopg, paramiko, P2P libs). Decide in Phase 0.

---

## Phase 0 — P2P Feasibility Spike (de-risk first)
Everything else depends on whether we can reach the NVR by serial number from our own server. The spec warns that DMSS working does not mean third-party P2P works, so we test this before building the app around it.

- Inspect source, license, platform support and real API of: OpenDahua, blackjid/dahua-p2p, dahua-smartpss-toolkit. Check for the official NetSDK (Windows/Linux, licensing).
- Standalone, redacted diagnostic CLI in `p2p-bridge/` (no app code).
- Try against the test NVR: P2P auth, device info, channel list, live RTSP/stream, recording search, recording download.
- Output: `docs/p2p-compatibility.md` with a verified / unsupported / unknown matrix, plus a recommendation for the bridge design.

**Needs from you:** test NVR serial + credentials (kept in a local `.env`, never committed), and whether the NVR is reachable from this PC.
**Gate:** we know which operations work over P2P, and which fallback (NetSDK, VPN, local connector) is needed.

## Phase 1 — Foundation
- Monorepo, git init, `.env.example`, docker-compose (Postgres, Redis).
- FastAPI + SQLAlchemy 2 + Alembic, UUID schema for core tables (users, campuses, nvrs, cameras, capabilities, audit_logs).
- Auth: login/logout/refresh, hashed passwords, admin bootstrap without default credentials, RBAC with per-campus permissions.
- React + Vite + TS + Tailwind + shadcn: login, app shell, sidebar, dark theme.
- Campus CRUD (API + UI), NVR registration record with encrypted credentials (pending status only).
- Tests: auth, permissions, campus CRUD.

**Gate:** log in, create campuses, register an NVR as "pending"; app works with no NVRs.

## Phase 2 — NVR Connectivity & Camera Inventory
- Adapter contract (`integrations/base.py`, factory, capability model: supported-tested / unsupported / unknown).
- Real adapter built from Phase 0 findings; fake adapter clearly labeled simulated.
- Celery registration workflow: connect, detect model, capabilities, discover channels, persist cameras (no duplicates, keep policies, mark missing cameras unavailable).
- NVR statuses, test / reconnect / discover endpoints, meaningful errors, redacted logs.
- UI: NVR management page, status indicators, Camera Management inventory page.

**Gate:** vertical slice from the spec works on the real NVR: login, campus, add NVR, diagnostics, camera discovery, DB, inventory page.

## Phase 3 — Live Monitoring
- P2P session manager (concurrency limits, no duplicate sessions, cleanup).
- Stream bridge into MediaMTX; WebRTC with HLS fallback; substream for grids, main stream for fullscreen; transcode only when needed.
- Stream session API with authorization (no NVR credentials in URLs).
- UI: camera wall (1/4/9/16/25 + pagination), filters, search, fullscreen, favorites, saved layouts, lazy-mounted players, truthful status and auto-reconnect.
- Concurrency test: find the real simultaneous-stream limit and document it.

**Gate:** several cameras across at least one campus stream in the browser; reconnect verified; limits documented.

## Phase 4 — SFTP Backups
- SFTP destinations: encrypted creds, host-key verification, test connection and write test.
- Recording search and download adapters (hardware-verified before enabling).
- Recordings page: search by campus / NVR / camera / time, manual backup, progress.
- Backup policies per camera (frequency, destination, mode), bulk update.
- Celery engine: checkpoints with overlap, dedup, staging, verify, upload, remote verify, retries with backoff, crash recovery, gap handling, skip in-progress recordings, disk checks, cancel.
- Backup Jobs / History pages with live progress (SSE/WebSocket).

**Gate:** scheduled and manual backups land on a real SFTP server, survive worker restart and network interruption.

## Phase 5 — Administration & Production
- Users & permissions UI, audit log viewer.
- Notifications (in-app first).
- Dashboard analytics and charts; honest "unknown" health state.
- Rate limiting, CSRF, observability and structured logs.
- Integration tests (Postgres, Celery, SFTP test server, stream authorization).
- Full Docker deployment with reverse proxy and TLS; Linux deployment docs; Windows setup docs; troubleshooting; API docs.

**Gate:** the 14-point Definition of Done in the spec.

---

## How we work each phase
1. Confirm scope and open questions.
2. Build in vertical slices (backend + DB + UI + tests).
3. Run lint, type-check and tests.
4. Report: files changed, tests run, how to start, hardware tests still required, known limitations.
5. You approve the gate, then we move on.
