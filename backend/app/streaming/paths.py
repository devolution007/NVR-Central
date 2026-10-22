"""MediaMTX path names and configurations for camera streams."""

import uuid
from typing import Any
from urllib.parse import quote

from app.integrations.base import ConnectionParams
from app.models.enums import StreamKind

# Substream is subtype=1 and main is subtype=0 on Dahua RTSP (verified on the test NVR).
SUBTYPE = {StreamKind.MAIN: 0, StreamKind.SUB: 1}

H264_NAMES = {"h.264", "h264", "avc"}
H265_NAMES = {"h.265", "h265", "hevc"}

# Encoder settings, chosen from measurements on the test NVR (H.265 704x576 substream, one stream, 9 s of real time):
#   default threads, veryfast        0.24 CPU cores      <- what FFmpeg does without guidance
#   ultrafast, default threads       0.68 cores          (thread spin makes "faster" presets slower)
#   1 thread, veryfast, crf 28       0.18 cores, 0.57 Mbit/s   <- chosen for substreams
# Many parallel FFmpeg processes with default threading saturated an 8-thread CPU at about 6 streams, so threads are
# pinned for both the decoder (before -i) and the encoder. The main stream (1080p) gets two threads and more headroom.
FFMPEG_TEMPLATES = {
    StreamKind.SUB: (
        "ffmpeg -nostdin -loglevel error -threads 1 -rtsp_transport tcp -i rtsp://127.0.0.1:$RTSP_PORT/{raw} "
        "-an -c:v libx264 -threads 1 -preset veryfast -tune zerolatency -crf 28 -maxrate 700k -bufsize 1400k "
        "-pix_fmt yuv420p -g 25 -keyint_min 25 -sc_threshold 0 "
        "-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
    ),
    StreamKind.MAIN: (
        "ffmpeg -nostdin -loglevel error -threads 2 -rtsp_transport tcp -i rtsp://127.0.0.1:$RTSP_PORT/{raw} "
        "-an -c:v libx264 -threads 2 -preset veryfast -tune zerolatency -crf 25 -maxrate 3000k -bufsize 6000k "
        "-pix_fmt yuv420p -g 25 -keyint_min 25 -sc_threshold 0 "
        "-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
    ),
}


def path_name(kind: str, camera_id: uuid.UUID, stream: StreamKind) -> str:
    return f"{kind}-{camera_id.hex}-{stream.value}"


def needs_transcode(codec: str | None, mode: str, client_hevc: bool = False) -> bool:
    """Decide whether the server must convert the camera's video to H.264 for this viewer.

    auto: no conversion for H.264 cameras; no conversion for H.265 cameras when the viewer's browser says it can play
    H.265 natively (it is passed through untouched, which costs the server almost nothing); otherwise convert. An
    unknown codec is converted because that is the safe choice."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    name = (codec or "").strip().lower()
    if name in H264_NAMES:
        return False
    return not (client_hevc and name in H265_NAMES)


def serve_kind(codec: str | None, transcode: bool) -> str:
    """MediaMTX path family. `live` carries H.264 (converted, or already H.264); `nat` carries the camera's own H.265
    for browsers that decode it themselves. They are separate paths so both kinds of viewer can watch one camera."""
    name = (codec or "").strip().lower()
    return "live" if transcode or name in H264_NAMES else "nat"


def rtsp_source_url(params: ConnectionParams, channel: int, stream: StreamKind) -> str:
    user, pw = quote(params.username, safe=""), quote(params.password, safe="")
    return f"rtsp://{user}:{pw}@{params.host}:{params.rtsp_port}/cam/realmonitor?channel={channel}&subtype={SUBTYPE[stream]}"


def source_conf(url: str) -> dict[str, Any]:
    """Pull from the NVR only while someone is watching, over TCP (Dahua RTSP over UDP is not used)."""
    return {
        "source": url,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": "10s",
        "sourceOnDemandCloseAfter": "10s",
        "rtspTransport": "tcp",
    }


def transcode_conf(raw_path: str, stream: StreamKind) -> dict[str, Any]:
    """Publish an H.264 copy of `raw_path`. FFmpeg reads MediaMTX over localhost, so it never sees NVR credentials."""
    return {
        "runOnDemand": FFMPEG_TEMPLATES[stream].format(raw=raw_path),
        "runOnDemandRestart": False,
        "runOnDemandStartTimeout": "15s",
        "runOnDemandCloseAfter": "5s",
    }
