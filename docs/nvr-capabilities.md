# NVR Capabilities (LAN / tunnel interface)

Filled in from real hardware runs of `p2p-bridge/diag/lan_diag.py`. First hardware run: **2026-10-21, DHI-NVR2116HS-4KS3, firmware 4.005.0000000.7.R (build 2026-06-16), over the LAN.** Only this one model and one NVR have been tested.

## Running the Phase 0b diagnostic

Prerequisite: a PC that can reach the NVR by IP (on the same LAN, or through a VPN). The NVR should be showing normal recordings.

1. Edit `p2p-bridge/.env` and set:
   ```
   NVR_HOST=<NVR LAN IP>
   NVR_USERNAME=<user>
   NVR_PASSWORD=<password>
   ```
   (`NVR_SERIAL` is only needed for the old P2P tool.) Use a dedicated read-only NVR account if one exists; the tool never changes settings, it only reads, searches, takes a snapshot, plays streams and downloads.
2. From `D:\NVR Central\p2p-bridge`:
   ```powershell
   .\.venv\Scripts\python diag\lan_diag.py --channels 1,2 --streams 1,2,4,8 --hours 24 --download --playback
   ```
   - `--channels` which channels to test (Dahua numbering is normally 1-based)
   - `--streams` concurrent substream counts to try; the first count that fails shows the NVR's limit
   - `--download` fetches the shortest found segment (capped at `--max-mb`, default 50)
   - `--playback` tests RTSP playback of the first found segment (an alternative export path)
3. The report is `p2p-bridge/reports/lan_<timestamp>.json` (host shown as `x.x.x.N`, serial/MAC/password masked). Share it, then copy the verified results into the table below.

## What each step answers

| Step | Question |
|---|---|
| `tcp_reachability` | Are HTTP, RTSP and 37777 open from here? |
| `cgi:*` | Model, firmware, serial (masked), NVR clock and timezone, channel names, remote cameras, stream encode settings, record capabilities |
| `snapshot` | Can we pull a JPEG per channel? |
| `rtsp_live(main/sub)` | Do both streams play, what codec (H.264/H.265), bitrate |
| `rtsp_concurrent_substreams` | How many simultaneous RTSP sessions before failures |
| `recording_search(channel_param=0/1/2)` | Is search 0- or 1-based, which stream types and file types are returned |
| `recording_download` | Throughput and whether the file is playable (ffprobe) |
| `rtsp_playback` | Does playback work as an export path |

## Results — DHI-NVR2116HS-4KS3, LAN, 2026-10-21

Status: **verified** = observed working on this NVR; **partial** = worked with limits noted; **failed**; **unknown** = not tested or not conclusive.

| Capability | Status | What we saw |
|---|---|---|
| HTTP CGI + digest auth | verified | All 11 probes answered. Account `admin`; a login-lockout feature is enabled on the NVR, so avoid hammering with wrong passwords |
| Model / firmware | verified | `getDeviceType`, `getSoftwareVersion` |
| NVR clock | verified | `global.cgi?action=getCurrentTime` matched the PC's local time |
| NVR timezone | partial | `NTP.TimeZone=7` ("Islamabad", i.e. UTC+5), NTP disabled. Value `7` is a Dahua enum index, so we need a mapping table before converting to UTC. Cross-checked only against the PC clock, not independently |
| Channel list / names | verified | `ChannelTitle` returns 16 names, 0-based (`ChannelTitle[0]` = channel 1) |
| Which channels have cameras | partial | `getCameraAll` returns 17 entries: 12 with a device model, 1 enabled without a model, 3 disabled, 1 odd trailing entry (channel 16). Its `Channel` field is 0 for every entry, so the **entry order is the channel order** (assumption, not proven). There is **no online/offline state** in this output and `getCameraState` returned HTTP 501 |
| Per-channel stream settings | verified | `Encode` config: main 1920x1080 H.265 2048 kbps 25 fps; sub 704x576 H.265 512 kbps 25 fps (channels 1 and 2 checked) |
| Snapshot | verified | JPEG via `snapshot.cgi?channel=N`, about 117 KB |
| RTSP live, substream | verified | `rtsp://HOST:554/cam/realmonitor?channel=N&subtype=1`, about 500 kbps, first data in under 0.1 s (channels 1 and 2) |
| RTSP live, main stream | verified | `subtype=0`, about 2 Mbps, first data in under 0.3 s |
| Codec | verified | **H.265 on both streams.** Browsers cannot reliably play H.265 over WebRTC/HLS, so Phase 3 needs transcoding to H.264 or a verified HEVC-capable path. Not yet tested |
| Concurrent RTSP sessions | partial | 8 simultaneous substreams all worked (about 4.5 Mbps total, about 560 kbps each, cycling channels 1 and 2). 16 not tested, so the real limit is unknown |
| Recording search | verified | `mediaFileFind.cgi`. **`condition.Channel` is 1-based** (0 returns HTTP 400); the result's `Channel` field is 0-based |
| Streams in search results | verified | Both `Main` and `Extra1` (substream) recordings exist, one pair per hour |
| Recording layout | verified | Hourly segments aligned to the hour, type `dav`, flag `Timing`. Per camera per hour: main about 908 MB, sub about 232 MB (about 21.8 GB and 5.6 GB per day) |
| In-progress segment | verified | The newest segment ended at the current time (15:46:16 at 15:46:36), so it is still being written. The backup engine must skip it |
| Recording download (HTTP) | **limited** | `RPC_Loadfile` works but **the NVR closes the connection part-way through a full hourly file** (measured 2026-10-23, HTTP and curl alike, not caused by live streams): substream hour files of about 235 MB stopped at 43,122,688 and 34,078,720 bytes, a main file of 931 MB at 123,731,968 bytes. The cut point is the same on every retry of one file but differs between files. No `Range` support (a range request returns the whole file from byte 0), so a download cannot be resumed or fetched in pieces. The earlier 24.8 Mbit/s result came from a short file. `loadfile.cgi` (time-range download) answers 200 with a Content-Length but sends no data. The backup engine detects the short transfer and reports the file as failed ("Incomplete download"); it never uploads a partial file |
| RTSP playback | partial | Works, but delivered at about real-time speed (2.0 Mbps). It is much slower than HTTP download, so it is not a practical export path |

## What this means for the design

1. **The standard interfaces cover almost everything**, so the tunnel plan is viable technically. The remaining unknown is the tunnel itself.
2. **Backup volume is large.** One NVR with 12 cameras on main streams is about 260 GB per day. At the measured 24.8 Mbit/s, downloading that takes about 23.5 hours of continuous transfer, so it barely keeps up even if the rate holds. Options to decide: back up the substream (about 67 GB per day for 12 cameras), back up only selected cameras (which the spec already intends), parallel downloads if the NVR allows, or run the backup agent at the campus (connector mode B) so the data goes LAN to SFTP.
3. **Live view needs H.265 handling** (transcode substreams to H.264, or confirm HEVC playback in target browsers).
4. **Timezone conversion** needs the NVR's TimeZone enum mapped to a UTC offset and stored per NVR.
5. **Camera online state** cannot be read from `getCameraAll`. Use a live probe (RTSP or snapshot) and show "unknown" until probed.
6. **Open tests:** 16 concurrent substreams, parallel downloads, full-hour download integrity and speed, camera online state, other NVR models.

## Adapter verification and the lockout incident (2026-10-21, later)

**Adapter run against the real NVR (read-only, correct credentials).** `DahuaDirectAdapter` returned: model
`DHI-NVR2116HS-4KS3`, firmware `4.005.0000000.7.R`, clock offset **UTC+05:00 measured from the NVR clock** (matching
timezone code 7, no warnings), and **13 cameras** (channels 1 to 13). Channels 1 to 12 answered RTSP DESCRIBE and
snapshots; channel 13 ("CAM 1", a configured camera of another type with no model string) did not answer within the
4 s timeout; channels 14 to 16 are disabled slots and are correctly not listed. This confirmed that **entry N of
`getCameraAll` is channel N+1**, and that disabled slots must be filtered on `DeviceInfo.Enable`, not only on the
address. Capabilities: device info, channel discovery, live video, recording search and snapshot verified; recording
download not tested (deliberately); P2P not used. All of this was with the earlier read-only diagnostic and adapter
runs, before the lockout below.

**Account lockout (important).** After roughly ten failed logins sent by a mis-configured set of automated tests
(since fixed and guarded against), the NVR answered **HTTP 403** to every request, including a correct login. That is
how this NVR reports a locked account (it has `LockLoginEnable=true`). The adapter now maps 403 to an authentication
error that says the account is probably locked and not to retry. Consequences for the design:
- an auth failure is never retried automatically, and periodic checks skip NVRs in `auth_failed`;
- the UI asks for confirmation before retesting an NVR whose credentials were rejected;
- tests can no longer reach any non-loopback address (`tests/support/scripted.py: NetworkBlockedAdapter`).

Open: how long the lock lasts on this firmware (not measured) and whether DMSS or the web UI are affected while it holds.
