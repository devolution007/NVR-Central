# P2P Compatibility — Phase 0 Findings

Status legend: **Verified (source)** = confirmed by reading the code; **Hardware: untested** = nothing has been run against a real NVR yet; nothing here is marked "operational" until a hardware run says so.

Research date: 2026-10-21. Repos were cloned and read, not just their READMEs.

## Candidates

| Candidate | Language / license | Platforms | Last commit seen | What it does (verified in source) | Verdict |
|---|---|---|---|---|---|
| **Tijn0/OpenDahua** 1.0.8 | Python ≥3.10 (declared 3.10–3.13), MIT; deps `cryptography`, `aiofiles`, `xmltodict` | Pure Python, Windows + Linux | 2026-06-10 | Dahua P2P handshake by serial, then **HTTP to the NVR's port 80 through the tunnel** with digest auth. Public API: `DahuaNVR(serial, user, pw)`, `connect/disconnect`, `get_nvr_name`, `get_time`, `get_videos(channel, start, end)`, `download_video(video, path)`. Search uses the `mediaFileFind` finder (create/find/read/close). Download uses `/cgi-bin/RPC_Loadfile/<path>`. | **Primary candidate** for device info, CGI calls, recording search, recording download |
| **blackjid/dahua-p2p** | Go 1.24, MIT | Linux Docker image provided (ghcr.io); Go not installed here, use Docker | 2026-09-20 | P2P handshake by serial, then **RTSP + interleaved RTP over the tunnel**. `cmd/dahua-p2p` is a local RTSP bridge (one per NVR, port 8554). Default 8 concurrent "realms" per tunnel, 32 clients max. Media goes direct to the device, relay is deliberately not used. | **Primary candidate for live video** (feeds MediaMTX / go2rtc) |
| **zkrige/dahua-smartpss-toolkit** | Python + shell, MIT | **macOS arm64 only**, needs a local SmartPSS install (proprietary dylibs) | single commit | Wraps SmartPSS NetSDK for snapshot/clip/list/download. Also a SmartPSS credential recovery script. | **Rejected**: not portable to Windows/Linux, depends on proprietary libraries, credential-recovery tooling is out of scope |
| **Official Dahua NetSDK** | C/C++ with wrappers, proprietary | Windows and Linux builds exist | n/a | Not downloaded or verified in this spike. It also needs P2P-capable builds and a license check. | **Fallback**, evaluate only if the two above fail on hardware |

## Findings that shape the design

1. **Two protocols, two tools.** OpenDahua tunnels HTTP (port 80); dahua-p2p tunnels RTSP (port 554). Neither does both. The design therefore needs one of each per NVR.
2. **Two P2P sessions to one NVR.** Using both means two P2P tunnels to the same device. dahua-p2p's own docs warn about starting "a competing tunnel for the same device". Whether Dahua firmware tolerates two simultaneous P2P sessions is **unknown** and is the top hardware question.
3. **Live concurrency ceiling.** dahua-p2p defaults to 8 concurrent streams per tunnel (its docs mention 7 running against an NVR for months). A 16-camera wall of substreams would exceed that, so the live wall needs on-demand streams, smaller default pages, or a higher tested limit. This is a hypothesis to measure, not a fact.
4. **OpenDahua gaps.**
   - No channel discovery, device model, or firmware call in the public API. The tunnel carries arbitrary HTTP, so standard Dahua CGI endpoints are probably reachable, but that is **unverified**. The diagnostic script probes seven read-only endpoints for this.
   - `get_videos` returns **main-stream only** and silently maps "no results" (HTTP 400) to an empty list.
   - `download_video` **buffers the whole file in RAM** before writing. Large segments need a streaming patch or a different path (see 5).
   - Channel indexing is passed straight through; whether it is 0- or 1-based is unverified.
   - Timestamps are NVR local time with no timezone. The spec needs UTC storage, so the NVR clock/offset must be read and recorded per NVR.
   - Not designed for concurrent use of one connection; its README warns that blocking I/O inside the `async with` block can kill the tunnel.
5. **Alternative download path (unverified).** Dahua RTSP supports playback URLs. If the dahua-p2p bridge can carry them, FFmpeg could record segments to disk without buffering. Test this on hardware.
6. **Maturity.** Both libraries are small (a handful of stars, few contributors). Plan for pinning exact versions, vendoring if needed, and keeping them behind the adapter interface.
7. **Python version.** Local Python is 3.14.6. OpenDahua declares 3.10–3.13 but installed and imports on 3.14 here. Production should still use 3.12 or 3.13.

## Capability matrix (current knowledge)

| Capability | Route | Source-verified | Hardware |
|---|---|---|---|
| P2P authentication | OpenDahua / dahua-p2p | yes | untested |
| Device name, clock | OpenDahua | yes | untested |
| Model / firmware | CGI via OpenDahua tunnel | no | untested |
| Channel discovery | CGI via OpenDahua tunnel | no | untested |
| Live video (substream/main) | dahua-p2p RTSP bridge | yes (README + code) | untested |
| Recording search | OpenDahua | yes (main stream only) | untested |
| Recording download | OpenDahua (RAM-buffered) or RTSP playback | partly | untested |
| Snapshot | CGI or RTSP | no | untested |
| Two simultaneous P2P sessions per NVR | n/a | no | **untested, critical** |

## Hardware test procedure (DHI-NVR2116HS-4KS3)

Prerequisite: the NVR is online in DMSS, and you know its serial, username and password. Put them in `p2p-bridge/.env` (copy from `.env.example`). The file is git-ignored.

```powershell
cd "D:\NVR Central\p2p-bridge"
.\.venv\Scripts\python diag\p2p_diag.py --cgi --channel 1 --hours 24
.\.venv\Scripts\python diag\p2p_diag.py --channel 0 --hours 24 --download
```

Output goes to `p2p-bridge/reports/diag_<timestamp>.json` with serial, password, MAC and token fields masked. Share that JSON (or the console output) to settle the matrix above.

Then test live video with the Go bridge in Docker (replace the placeholders locally, do not paste real values into files that get committed):

```powershell
docker run --rm -p 8554:8554 -e DAHUA_SERIAL=<serial> -e DAHUA_USERNAME=<user> -e DAHUA_PASSWORD=<password> ghcr.io/blackjid/dahua-p2p:latest
# In VLC: Media > Open Network Stream, with transport forced to TCP:
# rtsp://<user>:<password>@127.0.0.1:8554/cam/realmonitor?channel=1&subtype=1
```

Run the OpenDahua diagnostic and the bridge at the same time to answer the two-sessions question. Also try opening 2, 4, 8 and 9 streams to find the live ceiling.

## Open questions for hardware

1. Does the P2P handshake succeed for this NVR and account from this PC?
2. Which CGI endpoints respond over the tunnel (model, firmware, channels)?
3. Are search channels 0- or 1-based?
4. Do the OpenDahua session and the dahua-p2p bridge coexist on one NVR?
5. How many concurrent substreams work, and at what bitrate?
6. Does RTSP playback work through the bridge, and is it a better download path?
7. Substream codec (H.264 or H.265) and whether browser playback needs transcoding.
8. Does DMSS keep working while our sessions are open?
