# Backups (Phase 4)

Copies NVR recordings to an SFTP server. Scheduled per camera, or started by hand. Built and tested against a
SIMULATED NVR and a SIMULATED SFTP server. **Not proven end to end on real hardware: the test NVR cuts recording
downloads short (see "What the real NVR does" below) and no real SFTP server has been used yet.**

## How it works

```
NVR  --HTTP RPC_Loadfile-->  staging folder  --SFTP-->  server
        (streamed, 256 KiB)   size check, SHA-256       .part file, size check, rename, .sha256 sidecar
```

One **job** is one camera and one time window. It searches the NVR, drops recordings still being written, and for
each recording that is not already backed up: downloads it to the staging folder, checks the size against what the NVR
listed, hashes it, uploads under a temporary `.part` name, checks the size on the server, renames it into place, writes
`<name>.dav.sha256` next to it, and deletes the staged copy. Nothing is marked done before the server confirmed it.

Remote layout: `<base>/<campus>/<nvr>/ch<NN>-<camera>/<main|sub>/<yyyy>/<mm>/<dd>/<startUTC>_<endUTC>.dav`.
File names use UTC (`20261022T130000Z`). The NVR reports local time; it is converted with the offset measured from the
NVR's own clock (`nvrs.utc_offset_minutes`) and stored as UTC.

### Guarantees and how they are met

| Requirement | How |
|---|---|
| Never upload the same recording twice | Unique key (camera, destination, stream, start time). A finished row is skipped by every later job |
| Never lose a recording that was mid-write | Recordings ending within 2 minutes of the NVR clock are skipped; the checkpoint stops where the first skipped one starts |
| Survive a worker crash | Jobs write a heartbeat. A `running` job with no heartbeat for 5 minutes is put back in the queue by the sweeper (every minute), its half-done files go back to pending and the staged copies are deleted |
| Survive network loss | NVR unreachable or SFTP unreachable ends the job as failed with files left pending; the next run (with back-off: 2, 4, 8 ... up to 60 minutes) retries. A cut transfer fails that file and the run continues |
| Resume | A recording is re-downloaded from the start (the NVR does not support `Range`). Recordings already done are not touched |
| Do not hammer the NVR | One backup job per NVR at a time (database lock). A file that fails 5 times across runs is left failed until someone presses "Try this window again". Rejected credentials end the job and mark the NVR `auth_failed`; nothing retries |
| Disk safety | A recording is refused if it would leave less than `BACKUP_MIN_FREE_GB` free in the staging folder |
| Cancel | Checked between chunks; the partial file is deleted |
| Integrity | Size check against the NVR listing, SHA-256 stored in the database and in the `.sha256` sidecar, size verified on the server after upload. The server's copy is **not read back and re-hashed** (that would double the traffic) |
| Server identity | The SFTP host key's SHA-256 fingerprint is pinned. An administrator fetches it, compares it with the real server's, and confirms. A different key is refused |
| Gaps | If the NVR has no recording between two files for more than 2 minutes, the job message says so. The checkpoint passes gaps, because the NVR has nothing to back up there |

### The checkpoint

`camera_backup_policies.checkpoint_at`: everything recorded before it is safely backed up. Each scheduled run searches
from `checkpoint - 10 minutes` to now. It advances only over recordings that are done, so a failed recording holds it
back and is retried. Turning a policy on starts from "now" unless a start-from of up to 168 hours is given.

## Running it

Backups run on their own Celery queue so a long download never delays NVR checks:

```powershell
celery -A app.workers.celery_app worker -Q celery --pool=solo --loglevel=info      # NVR checks, discovery
celery -A app.workers.celery_app worker -Q backups --pool=solo --loglevel=info     # backups
celery -A app.workers.celery_app beat                                              # schedules both
```

(For a quick start one worker can serve both: `-Q celery,backups`. On Windows the `solo` pool runs one task at a time,
so a backup then blocks NVR checks until it finishes.) The pages warn when no worker listens to the `backups` queue.

Staging folder: `BACKUP_STAGING_DIR` (default `data/backup-staging`). Put it on a disk with room for one recording per
running job. Settings are in `.env.example`.

To try the pages without an SFTP server: `backend\.venv\Scripts\python scripts\sim_sftp.py` (SIMULATED server on
127.0.0.1:12222, user `backup`, password `sftp-secret`).

## What the real NVR does (measured 2026-10-23, DHI-NVR2116HS-4KS3)

- **Search works** (`mediaFileFind`). Sizes in the listing are in bytes and match the real files' `Content-Length`.
- **A full-file download cannot be completed.** `RPC_Loadfile` sends the headers and then closes the connection
  part-way. Substream hour files (about 235 MB) stopped at 43,122,688, 34,078,720 and 0 bytes; a 931 MB main-stream
  file stopped at 123,731,968 bytes. The cut point is the same on every retry of one file and different for different
  files. It is the same with `curl`, with the live wall stopped, and at any read speed. `Range` is ignored, so the file
  cannot be fetched in pieces. `loadfile.cgi` returns 200 and a Content-Length but no data.
- **RTSP playback** returned video for a 2 minute window once and failed to open on later tries; its speed was not
  established. It is not implemented.
- The engine therefore **fails each such recording with a clear message** ("The NVR closed the connection after N of M
  bytes"), never uploads a partial file, and after 5 attempts leaves it for a manual retry.

The cause is unknown. A disk or file-system problem on the NVR would explain a per-file cut point; checking the HDD
status on the NVR itself is worth doing. Other NVR models may behave differently.

## Not built / not verified

- No real SFTP server has been used. Key login is implemented and tested only for host-key and password paths in
  the simulator (the key parser has no test with a real key file).
- Live progress is polling every 2 seconds, not SSE/WebSocket.
- Connector mode B (a backup agent at the campus) is not built. It would solve the download problem only if the agent
  can read the files another way; it does not help with the NVR cutting HTTP downloads.
- Retention / deleting old backups is not built. The app never deletes anything on the SFTP server.
- Parallel downloads from one NVR are not allowed (untested on hardware).
- Throughput over a real tunnel is unmeasured.
