# Live monitoring (Phase 3)

## How a picture gets to the browser

```text
Browser (hls.js)  ->  FastAPI  ->  MediaMTX  ->  NVR (RTSP over TCP)
 cookie + session     checks the     pulls on       substream = subtype 1
 id in the URL        user, campus,  demand, can    main stream = subtype 0
                      and session    convert
```

- The browser only ever sees `/api/v1/stream-sessions/<id>/hls/...`. No NVR address, username or password reaches it.
- Every playlist and segment request re-checks: logged in, session belongs to the caller, session not ended or expired,
  and the caller can still see the camera's campus (revoking access cuts a running stream within seconds).
- MediaMTX is bound to `127.0.0.1` (or the internal Docker network) and has no accounts. It must never be exposed.
- MediaMTX pulls from the NVR **only while someone watches** and stops about 10 to 15 seconds after the last viewer.
- NVR credentials exist in MediaMTX memory (path configuration) and are re-applied at every session start. They are
  removed when the NVR is edited, disabled or deleted.

## H.265, and what it costs

Every stream on the test NVR is H.265. The browser tells the server whether it can play H.265 (`native_hevc`):

| Browser | What happens | Server cost |
|---|---|---|
| Can decode H.265 (Chrome/Edge 107+ with hardware support, Safari) | The camera stream is passed through untouched | about 0.25 of one CPU core for 8 streams |
| Cannot | FFmpeg converts to H.264 on the server | see below |

Measured on the dev PC (Intel i5-1145G7, 8 threads), real cameras, 704x576 H.265 substreams:

| Setup | Result |
|---|---|
| Native H.265 passthrough, 8 tiles live | gateway **20 to 30 % of one core**, 0 FFmpeg processes |
| Converted, default FFmpeg threading, 6 to 8 streams | **520 to 780 % CPU**, tiles stalled and restarted in a loop |
| One converted stream, run alone | 0.24 core (default), 0.18 core and 0.57 Mbit/s (1 thread, veryfast, crf 28) |
| The same tuned conversion with many streams running | about 0.8 core each: thread contention inflates it |

Lessons built into the code:
- FFmpeg threads are pinned (decoder and encoder). "Faster" presets with default threads were *slower* (ultrafast 0.68 core).
- Stream starts are admitted three at a time in the browser. Opening a whole wall at once saturated the server, made
  segments late, tripped the stall detector, and restarted everything.
- A stall gets a nudge first and a rebuild only if it persists (20 s each).
- Plan for converted streams as roughly 0.2 to 0.8 core each. A server that converts a 16-tile wall needs real CPU, or
  clients that can decode H.265.

Not measured: converting the 1080p main stream (it is configured with two threads and a higher quality cap).

## Limits

- Each NVR can override the limit in its settings (NVR management → Edit → "Max live streams", 1 to 64; blank uses the default below).
- `MAX_STREAMS_PER_NVR` (default 8, used when an NVR has no limit of its own) counts distinct (camera, stream, delivery) upstream sessions. Viewers of the same
  stream share one. 8 simultaneous streams through the gateway worked on the test NVR. 16 and 24 were **not** tested
  (someone was watching live at the time, so the NVR was not loaded further). Raise the limit only after testing.
- Opening a camera fullscreen pauses the wall behind it, so the HD stream does not compete with the tiles.
- The 9th tile of one NVR shows "This NVR already has 8 live streams" with a Try again button. It is not retried automatically.

## Behaviour worth knowing

- NVRs whose credentials were rejected, that were never reached, that are disabled or that are P2P cannot stream. The
  reason is shown and the NVR is not contacted.
- A camera last reported not responding is not auto-streamed; the tile offers "Try anyway".
- Off-screen tiles and tabs hidden for more than 30 seconds hold no stream.
- Sessions expire 60 s after the last keepalive (sent every 20 s). A closed tab ends its sessions through a keepalive
  `fetch` on `pagehide`.
- Favorites and saved layouts are per user. Layouts store camera ids; cameras that disappear or lose access are skipped.

## Things found while building it (so they are not rediscovered)

- MediaMTX answers the first HLS request with a 302 "cookie check" redirect and tags every playlist URL with
  `?session=<uuid>`. The proxy sends `cookieCheck=1` up front, follows redirects as a fallback, and forwards only a
  well-formed `session` UUID.
- A single `video.play()` right after the manifest can be rejected, so playback also starts on `canplay` and the
  element has `autoplay`.
- hls.js requests bypass the app's token refresh, so a 401 mid-stream triggers an ordinary API call to refresh the login
  and then resumes loading.

## Not done or not verified

- WebRTC (HLS only; latency about 3 to 5 s).
- Audio (the cameras send G.711; HLS drops it).
- Safari's native HLS path and Firefox are untested in a real browser.
- A reverse proxy in front of the backend must not buffer responses, or live video will lag. Not tested.
- Behaviour over a real campus uplink or tunnel (bandwidth for N streams) is unmeasured.
- A converted 1080p main stream and 16+ simultaneous NVR streams are unmeasured.
