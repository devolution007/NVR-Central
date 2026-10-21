"""Administrative commands.

  python -m app.cli create-admin --username alice [--display-name "Alice"]

The first administrator is created here, never through a default account. The password is
read from a hidden prompt, or from the NVR_ADMIN_PASSWORD environment variable for scripted setup.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from app.core.security import PasswordPolicyError, hash_password, validate_password_policy
from app.db.session import get_sessionmaker
from app.models.enums import Role
from app.models.user import User


def create_admin(username: str, display_name: str) -> int:
    username = username.strip().lower()
    password = os.environ.get("NVR_ADMIN_PASSWORD") or getpass.getpass("New admin password: ")
    if not os.environ.get("NVR_ADMIN_PASSWORD") and getpass.getpass("Repeat password: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        validate_password_policy(password, username)
    except PasswordPolicyError as exc:
        print(f"Rejected: {exc}", file=sys.stderr)
        return 1

    with get_sessionmaker()() as db:
        exists = db.scalar(
            select(func.count())
            .select_from(User)
            .where(func.lower(User.username) == username, User.deleted_at.is_(None))
        )
        if exists:
            print(f"User '{username}' already exists.", file=sys.stderr)
            return 1
        db.add(
            User(
                username=username,
                display_name=display_name or username,
                password_hash=hash_password(password),
                role=Role.ADMIN,
                is_active=True,
            )
        )
        db.commit()
    print(f"Administrator '{username}' created.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create an administrator account")
    p.add_argument("--username", required=True)
    p.add_argument("--display-name", default="")
    args = parser.parse_args()
    if args.command == "create-admin":
        return create_admin(args.username, args.display_name)
    return 2


if __name__ == "__main__":
    sys.exit(main())
