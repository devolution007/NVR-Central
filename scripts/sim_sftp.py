"""Start a SIMULATED SFTP server on 127.0.0.1 for trying the backup pages without a real SFTP server.

    backend\\.venv\\Scripts\\python scripts\\sim_sftp.py

Login: user "backup", password "sftp-secret". Files are stored in data\\sim-sftp (deleted when you stop it).
In the app add a destination with address 127.0.0.1, port 12222, then "Fetch host key". Needs ALLOW_LOOPBACK_NVR_HOSTS=true
in .env (development only). This is for development only: it proves nothing about a real SFTP product.
"""

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from tests.support.fake_sftp import PASSWORD, USER, FakeSftpServer  # noqa: E402

PORT = 12222
store = ROOT / "data" / "sim-sftp"
shutil.rmtree(store, ignore_errors=True)
store.mkdir(parents=True)
server = FakeSftpServer(store, port=PORT).start()
print(f"SIMULATED SFTP server on 127.0.0.1:{server.port}  user {USER} / password {PASSWORD}")
print(f"Host key fingerprint: {server.fingerprint}")
print(f"Files land in {store}. Ctrl+C to stop.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.stop()
    shutil.rmtree(store, ignore_errors=True)
