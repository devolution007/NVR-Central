# NVR Central

Self-hosted web app for managing Dahua NVRs across multiple campuses: central live monitoring and SFTP backups.

**Status:** Phase 2 built (NVR connection tests, camera discovery, campus tunnel management). Live video and backups are not built yet. See [docs/PHASES.md](docs/PHASES.md).

| Area | Stack |
|---|---|
| Frontend | React 19, TypeScript, Vite, Tailwind 4, shadcn/ui, React Router, TanStack Query, Zustand, React Hook Form + Zod |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, Pydantic, PostgreSQL |
| Later phases | Redis/Celery, MediaMTX, FFmpeg, WireGuard, SFTP |

## Quick start (Windows)

Prerequisites: Docker Desktop (running), Python 3.12+, Node 22+.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-setup.ps1
```

This generates `.env` with fresh secrets, starts PostgreSQL and Redis in Docker, installs dependencies and applies migrations. Then:

```powershell
cd backend
.\.venv\Scripts\python -m app.cli create-admin --username yourname     # prompts for a password (12+ chars)
.\.venv\Scripts\uvicorn app.main:app --reload                          # API on http://127.0.0.1:8000

cd ..\backend
.\.venv\Scripts\celery -A app.workers.celery_app worker --pool=solo --loglevel=info   # background worker

cd ..\frontend
npm run dev                                                            # UI on http://localhost:5173
```

NVR connection tests and camera discovery run in the Celery worker, so keep it running (and Redis up), or the Test/Discover buttons report the queue as unavailable. Optional scheduler for periodic health checks: `celery -A app.workers.celery_app beat`.

**Trying it without hardware:** `backend\.venv\Scripts\python scripts\sim_nvr.py` starts a clearly labelled SIMULATED NVR on `127.0.0.1` (HTTP 18080, RTSP 18554, user `admin`, password `secret`). Register it with connection type "Direct LAN", address `127.0.0.1`. This needs `ALLOW_LOOPBACK_NVR_HOSTS=true` (development only, refused in production).

**Never point tests or experiments at a real NVR with wrong credentials.** Dahua NVRs lock the account after a few failed logins, which also blocks DMSS. The app does not retry rejected credentials automatically.

Open **http://localhost:5173** (use `localhost`). The Vite dev server proxies `/api` to the backend so session cookies stay same-origin. API docs: http://127.0.0.1:8000/api/docs (development only).

There is no default account. The first administrator is created with the CLI above.

### Everything in containers

```powershell
docker compose --profile app up -d --build
```

### Ports

PostgreSQL `127.0.0.1:15432`, Redis `127.0.0.1:16379`, API `8000`, UI `5173`. All bound to localhost. High database ports are deliberate: `5432` is often taken and Windows reserves random ports above 49152.

## Tests and checks

```powershell
cd backend
.\.venv\Scripts\python -m pytest          # needs the db container; creates <db>_test automatically
.\.venv\Scripts\ruff check .
.\.venv\Scripts\mypy app

cd ..\frontend
npm run typecheck; npm run lint; npm test; npm run build
```

## Security model (Phase 1)

- Passwords hashed with Argon2; 12 character minimum; account lock after 5 failures; per-IP login rate limit (in-process, moves to Redis in Phase 5).
- Sessions: short-lived access token and rotating refresh token in HttpOnly cookies, refresh tokens stored hashed, reuse of a rotated token revokes all of that user's sessions. CSRF double-submit header on all state-changing requests.
- NVR passwords and P2P serials are encrypted at rest (Fernet, key in `ENCRYPTION_KEY`) and never returned by the API. **Back up `ENCRYPTION_KEY` separately; without it stored NVR credentials cannot be recovered.**
- Roles: admin (everything), operator and viewer (read access limited to assigned campuses; writes arrive per feature in later phases). Campus access is enforced on every query, and a campus you cannot see returns 404.
- Audit log records logins, campus/NVR/user changes, with no secrets.

## Layout

```
backend/    FastAPI app, models, Alembic migrations, tests
frontend/   React app
p2p-bridge/ Phase 0 connectivity diagnostics (P2P and LAN)
docs/       Phase plan, P2P findings, NVR capability results
scripts/    env generation, Windows setup
```

## Docs

- [docs/PHASES.md](docs/PHASES.md): phased plan and decisions
- [docs/p2p-compatibility.md](docs/p2p-compatibility.md): why direct P2P is not the primary path
- [docs/nvr-capabilities.md](docs/nvr-capabilities.md): measured behaviour of the test NVR over its LAN interfaces
- [docs/architecture.md](docs/architecture.md): Phase 1 design notes and deviations from the spec
