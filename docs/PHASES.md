# NVR Central — Phased Delivery Plan (revision 2, 2026-10-21)

Source spec: `Master Prompt for NVR Centeral App.txt`. Each phase ends with a working, demoable result and an exit gate. We do not start the next phase until the gate is passed.

Rule from the spec: nothing hardware-dependent is marked "operational" until it passes a real test against the DHI-NVR2116HS-4KS3.

## What changed in this revision

Phase 0 tested Dahua P2P against the test NVR with both candidate libraries. **Neither connected** (details in `docs/p2p-compatibility.md`):

- OpenDahua: cloud handshake works, direct UDP contact with the NVR times out. Same result from two different PC networks.
- dahua-p2p (Go bridge): fails earlier with `p2p channel error: Not Found`.

Decision: **direct P2P is no longer the primary connection path.** It becomes an optional, experimental adapter that is only enabled for an NVR where it has passed a hardware test. The primary path is reaching the NVR over a private tunnel from the campus to the central server, then talking to it with Dahua's standard LAN interfaces (HTTP CGI and RTSP).

## Connectivity strategy

```text
Campus A LAN                         Central server (public IP)
 NVR 192.168.x.x  <--- LAN ---> [Connector] ==outbound WireGuard==> [WireGuard hub] --> FastAPI / workers / MediaMTX
Campus B LAN                                                          (private 10.x tunnel network)
 NVR 192.168.y.y  <--- LAN ---> [Connector] ==outbound WireGuard==>
```

Why this fits the constraints:
- The tunnel is **outbound from the campus**, so it works behind NAT and CGNAT. No ports are opened on campus routers and nothing is exposed to the public internet (spec requirement).
- Once the tunnel is up, the central server reaches each NVR by its private address using documented, stable interfaces: HTTP CGI on port 80/443 (device info, channels, recording search and download) and RTSP on port 554 (live and playback). No reverse-engineered protocol is in the critical path.
- Dahua NVRs generally cannot run a VPN client themselves, so something at the campus must hold the tunnel. Options, in order of preference:
  1. **Router with a built-in WireGuard or OpenVPN client** (no new hardware). Needs checking per campus.
  2. **Connector device**: a small always-on box (Raspberry Pi 4/5, mini PC, or an existing always-on PC) running our connector package: WireGuard client, health reporting, optional local backup agent.
  3. **P2P adapter** only where hardware testing proves it works.
- Overlapping campus subnets (every site using 192.168.1.0/24) are expected. The hub gives each campus a unique tunnel address and the connector performs NAT to the NVR, so the app always addresses an NVR by its tunnel IP.

**Connector modes** (a campus can start in A and move to B):
- **A. Network bridge only.** Connector just holds the tunnel and forwards to the NVR. All video and backup traffic goes to the central server.
- **B. Backup agent.** Connector also runs the backup worker locally: it searches and downloads recordings on the LAN and uploads straight to SFTP, reporting status to central. Saves campus upload bandwidth through the central server and survives tunnel drops. Live view still uses the tunnel.

Costs and risks to be honest about:
- Needs one device or router change per campus. This reverses the spec's "no hardware at campuses for the primary implementation" and needs your approval.
- Live view from many campuses uses the campus upload bandwidth. Grids must use substreams.
- The NVR may limit simultaneous RTSP sessions. Unknown until tested.

## Environment (checked 2026-10-21)
- Docker 29.8 (Docker Desktop must be started manually), Node 24.18, Python 3.14.6.
- Spec requires Python 3.12+. Use a 3.12 or 3.13 venv/Docker image for the backend.
- Project `.env` for the P2P diagnostics lives in `p2p-bridge/.env` (git-ignored).

---

## Phase 0 — Connectivity Feasibility  (0a done, 0b next)

**0a — P2P spike: DONE, result negative for this NVR.** Findings in `docs/p2p-compatibility.md`. Reusable output: Windows event-loop fix, redacted diagnostic tool.

**0b — LAN interface validation: DONE on the test NVR (2026-10-21). Results in `docs/nvr-capabilities.md`. Remaining open tests are listed there.**
- Build `p2p-bridge/diag/lan_diag.py` (rename folder to `connectivity/` later): talks to the NVR by IP using HTTP digest auth and RTSP, no third-party Dahua library.
- Verify against the DHI-NVR2116HS-4KS3: model, firmware, serial (masked), time and timezone, channel list and names, per-channel stream settings (main/sub codec, resolution), RTSP live main and sub (open 1, 2, 4, 8, 9 at once to find the session limit), recording search (channel indexing, types, main vs sub), recording download (streamed to disk, throughput), RTSP playback as an alternative export, snapshot.
- Output: capability matrix with real results in `docs/nvr-capabilities.md`.

**Needs from you:** a way to reach the NVR by IP for one test. Simplest: run the diagnostic from a PC on the NVR's LAN (carry the repo on a laptop, or remote into a campus PC). Also its LAN IP (any value; kept in `.env`).
**Gate:** each capability above is marked verified, unsupported, or unknown for this model.

## Phase 1 — Foundation  (DONE 2026-10-21, awaiting your sign-off)
- Monorepo, git init, `.env.example`, docker-compose (Postgres, Redis).
- FastAPI + SQLAlchemy 2 + Alembic, UUID schema for core tables.
- Auth: login/logout/refresh, hashed passwords, admin bootstrap without default credentials, RBAC with per-campus permissions.
- React + Vite + TS + Tailwind + shadcn: login, app shell, sidebar, dark theme.
- Campus CRUD (API + UI).
- **NVR record uses a connection profile, not just a serial:** `connection_type` (`tunnel` | `lan` | `p2p`), host/port, credentials (encrypted), optional serial (P2P only), tunnel peer reference.
- Tests: auth, permissions, campus CRUD.

**Gate:** log in, create campuses, register an NVR as "pending" with a connection profile; app works with no NVRs.

**Result:** gate met. Backend 51 tests, frontend 30 tests, ruff/mypy-strict/tsc/oxlint clean. See `README.md` and `docs/architecture.md`. Decision recorded: camera backup stream is selectable per camera (`sub` default, `main` optional); the selection UI arrives in Phase 4.

## Phase 2 — Campus Connectivity & NVR Inventory  (BUILT 2026-10-21, awaiting your sign-off)
**2A — Tunnel infrastructure**
- WireGuard hub in docker-compose (Linux) with a documented Windows dev setup.
- Peer management: create a campus peer, generate keys and a downloadable config / QR, assign tunnel address, revoke.
- Connector package v1 (mode A): WireGuard client config + NAT to the NVR LAN IP, Raspberry Pi/mini-PC install guide, router-WireGuard guide.
- Tunnel health: last handshake, latency, status shown in the UI. "Unknown" is distinct from "offline".

**2B — Adapter and discovery**
- Adapter contract (`integrations/base.py`, factory, capability model: supported-tested / unsupported / unknown).
- **`dahua_direct` adapter** (HTTP digest CGI + RTSP URLs) built from the 0b results. `dahua_p2p` adapter skeleton stays disabled and labeled experimental. Simulated adapter for tests, clearly labeled.
- Celery registration workflow: connect, detect model, capabilities, discover channels, persist cameras (no duplicates, keep policies, mark missing cameras unavailable).
- NVR statuses, test / reconnect / discover endpoints, meaningful errors, redacted logs.
- UI: Campus connectivity page, NVR management, Camera Management inventory.

**Result (honest status):**
- 2B (adapter, workflow, discovery, inventory UI): built and tested. The Dahua adapter was run read-only against the real NVR and found the right 13 cameras; the full Celery workflow and UI were exercised against a SIMULATED NVR. **The full app workflow was then run once against the real NVR (2026-10-21, one Reconnect from the UI/API): status Connected, model and firmware read, UTC+05:00, 13 cameras (12 online, 1 not responding), capabilities as expected, download not tested.**
- 2A (tunnels): peer management, key and config generation, hub compose overlay and health parsing are built and unit tested. **Nothing has been brought up against a real hub or campus device.** See docs/tunnel.md.

**Gate:** the first vertical slice from the spec works on the real NVR through a tunnel: login, campus, tunnel peer, add NVR, diagnostics, camera discovery, DB, inventory page.

## Phase 3 — Live Monitoring  (BUILT 2026-10-22, awaiting your sign-off)
- MediaMTX pulls RTSP from each NVR over the tunnel. No custom stream bridge is needed on this path.
- Stream session API with authorization (no NVR credentials in browser URLs); credentials stay in MediaMTX-side config generated by the backend.
- WebRTC with HLS fallback; substream for grids, main stream for fullscreen; transcode only when needed (H.265 handling).
- Session manager: per-NVR concurrency limit from the 0b measurement, on-demand start/stop, no duplicate pulls.
- UI: camera wall (1/4/9/16/25 + pagination), filters, search, fullscreen, favorites, saved layouts, lazy-mounted players, truthful status and auto-reconnect.
- Measure per-campus bandwidth for N substreams and document it.

**Gate:** several cameras across at least one campus stream in the browser over the tunnel; reconnect verified; limits documented.

**Result (honest status):** live viewing works end to end on the real NVR over the LAN (8 simultaneous tiles, fullscreen main stream 1920x1080, favorites, layouts). Details, measurements and gaps are in `docs/live-monitoring.md`. **Not** proven: streaming through a tunnel, 16+ simultaneous NVR streams, converted main stream CPU, WebRTC (HLS only), audio.

## Phase 4 — SFTP Backups  (BUILT 2026-10-23, real-NVR download blocked, awaiting your decision)
- SFTP destinations: encrypted creds, host-key verification, test connection and write test.
- Recording search and download via the direct adapter, **streamed to disk** (never fully in RAM).
- Recordings page: search by campus / NVR / camera / time, manual backup, progress.
- Backup policies per camera (frequency, destination, mode), bulk update.
- Celery engine: checkpoints with overlap, dedup, staging, verify, upload, remote verify, retries with backoff, crash recovery, gap handling, skip in-progress recordings, disk checks, cancel.
- Timezone handling: NVR local time converted with the NVR's own offset, stored as UTC.
- Backup Jobs / History pages with live progress (SSE/WebSocket).
- **Connector mode B (backup agent)** as the last item, only if tunnel bandwidth or reliability from 0b/3 shows it is needed.

**Gate:** scheduled and manual backups land on a real SFTP server and survive worker restart and tunnel/network interruption.

**Result (honest status):** destinations (pinned host keys, test with write check), recording search, manual and scheduled backups, the engine (dedup, checkpoints, in-progress skip, crash recovery, retries with back-off, cancel, disk guard, sidecar hashes), policies with bulk edit, Recording Browser, Backup Jobs and History pages are built. Backend 263 tests and frontend tests pass against a SIMULATED NVR and SIMULATED SFTP server. **On the real NVR a full-file HTTP download fails: the NVR closes the connection part-way (at a file-specific point, no `Range` support).** Search works. The engine reports this honestly and uploads nothing partial. No real SFTP server has been used. Details and options in `docs/backups.md`. Not built: SSE (polling instead), connector mode B, retention.

## Phase 5 — Administration & Production
- Users & permissions UI, audit log viewer.
- Notifications (in-app first), including tunnel-down and connector-offline alerts.
- Dashboard analytics and charts; honest "unknown" health state.
- Rate limiting, CSRF, observability and structured logs.
- Integration tests (Postgres, Celery, SFTP test server, stream authorization, tunnel peer lifecycle).
- Full Docker deployment with reverse proxy and TLS; Linux deployment docs; Windows setup docs; campus onboarding guide; troubleshooting; API docs.

**Gate:** the 14-point Definition of Done in the spec, with "register NVR using P2P serial" interpreted as "register via tunnel (or P2P where proven)".

---

## Decisions needed from you
1. **Approve one device or router change per campus** (or tell me campuses where that is impossible; those would stay P2P-only or unsupported).
2. **Hub location:** the central server needs a public IP and one open UDP port for WireGuard. Do you already have the VPS, or is the first deployment the Windows PC? (A Windows PC behind NAT cannot be the hub; for development we can test the tunnel on a LAN or with a temporary VPS.)
3. **How to run Phase 0b:** from a PC on the NVR's LAN, or remote into one there.
4. Parked: whether to keep digging into why the two P2P libraries disagree. Low priority now.

## How we work each phase
1. Confirm scope and open questions.
2. Build in vertical slices (backend + DB + UI + tests).
3. Run lint, type-check and tests.
4. Report: files changed, tests run, how to start, hardware tests still required, known limitations.
5. You approve the gate, then we move on.
