"""Create .env from .env.example with fresh random secrets.

Usage:  python scripts/init-env.py [--force]

Only blank values are filled. An existing .env is never overwritten unless --force
is given (which would make previously encrypted NVR credentials unreadable).
"""
from __future__ import annotations

import argparse
import base64
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="overwrite an existing .env")
    args = ap.parse_args()

    target = ROOT / ".env"
    if target.exists() and not args.force:
        print(f"{target} already exists; not touching it (use --force to regenerate).")
        return 0

    values: dict[str, str] = {}
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k] = v

    values["POSTGRES_PASSWORD"] = values["POSTGRES_PASSWORD"] or secrets.token_urlsafe(24)
    values["SECRET_KEY"] = values["SECRET_KEY"] or secrets.token_urlsafe(48)
    values["ENCRYPTION_KEY"] = values["ENCRYPTION_KEY"] or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if not values["DATABASE_URL"]:
        values["DATABASE_URL"] = (
            f"postgresql+psycopg://{values['POSTGRES_USER']}:{values['POSTGRES_PASSWORD']}"
            f"@localhost:{values['POSTGRES_HOST_PORT']}/{values['POSTGRES_DB']}"
        )

    out = []
    for line in lines:
        if line and not line.startswith("#") and "=" in line:
            k = line.split("=", 1)[0]
            out.append(f"{k}={values[k]}")
        else:
            out.append(line)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {target} with generated secrets. Back up ENCRYPTION_KEY somewhere safe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
