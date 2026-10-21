r"""SIMULATED Dahua NVR for trying the app without real hardware. Development only.

  backend\.venv\Scripts\python scripts\sim_nvr.py

Then register an NVR in the app with: connection "Direct LAN", address 127.0.0.1, HTTP port 18080, RTSP port 18554,
username admin, password secret. Needs ALLOW_LOOPBACK_NVR_HOSTS=true in .env (refused when ENVIRONMENT=production).
Everything it reports is invented: a camera named "Lobby" here is not a real camera.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from tests.support.fake_dahua import FakeDahuaServer, SimCamera, SimNvr  # noqa: E402

nvr = SimNvr(
    device_type="SIMULATED-NVR-DEMO",
    capacity=8,
    cameras=[
        SimCamera("Lobby (simulated)", "SIM-IPC-A"),
        SimCamera("Car park (simulated)", "SIM-IPC-B"),
        SimCamera("Back gate (simulated)", "SIM-IPC-B", rtsp_ok=False),
        SimCamera("Disabled slot", enable=False),
    ],
)
server = FakeDahuaServer(nvr, http_port=18080, rtsp_port=18554).start()
print(f"SIMULATED NVR running: HTTP {server.http_port}, RTSP {server.rtsp_port}, user admin / password secret. Ctrl+C to stop.")
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    server.stop()
