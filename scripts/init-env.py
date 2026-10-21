"""Create or complete .env from .env.example with fresh random secrets.

Usage:  python scripts/init-env.py [--force]

Blank values are filled with generated secrets. An existing .env keeps every value it already has: keys that are
missing (added by a newer version of .env.example) are appended, nothing else changes. --force regenerates
everything, which makes previously encrypted NVR credentials unreadable.
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_pairs(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def fill_defaults(values: dict[str, str]) -> None:
    values["POSTGRES_PASSWORD"] = values.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(24)
    values["SECRET_KEY"] = values.get("SECRET_KEY") or secrets.token_urlsafe(48)
    values["ENCRYPTION_KEY"] = values.get("ENCRYPTION_KEY") or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    if not values.get("DATABASE_URL"):
        values["DATABASE_URL"] = (
            f"postgresql+psycopg://{values['POSTGRES_USER']}:{values['POSTGRES_PASSWORD']}"
            f"@127.0.0.1:{values['POSTGRES_HOST_PORT']}/{values['POSTGRES_DB']}"
        )
    if not values.get("REDIS_URL"):
        values["REDIS_URL"] = f"redis://127.0.0.1:{values['REDIS_HOST_PORT']}/0"
    if not values.get("WG_HUB_PRIVATE_KEY"):
        # WireGuard private keys are 32 random bytes; wg clamps them itself.
        values["WG_HUB_PRIVATE_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate everything (breaks stored encrypted credentials)")
    args = ap.parse_args()

    target = ROOT / ".env"
    example = ROOT / ".env.example"
    example_lines = example.read_text(encoding="utf-8").splitlines()
    example_values = read_pairs(example)

    if target.exists() and not args.force:
        current = read_pairs(target)
        merged = {**example_values, **{k: v for k, v in current.items() if v != ""}}
        fill_defaults(merged)
        missing = [k for k in example_values if k not in current or (current[k] == "" and merged[k] != "")]
        if not missing:
            print(f"{target} is up to date.")
            return 0
        text = target.read_text(encoding="utf-8").rstrip("\n").splitlines()
        text = [f"{ln.split('=', 1)[0]}={merged[ln.split('=', 1)[0]]}" if "=" in ln and not ln.startswith("#") and ln.split("=", 1)[0] in missing and ln.split("=", 1)[1] == "" else ln for ln in text]
        appended = [k for k in missing if k not in current]
        if appended:
            text += ["", "# added by scripts/init-env.py"] + [f"{k}={merged[k]}" for k in appended]
        target.write_text("\n".join(text) + "\n", encoding="utf-8")
        print(f"Updated {target}: filled {', '.join(missing)}. Existing values were not changed.")
        return 0

    values = dict(example_values)
    fill_defaults(values)
    out = []
    for line in example_lines:
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
