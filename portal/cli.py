"""Staff accounts from the command line: python -m portal.cli create-staff EMAIL ROLE (password asked, not echoed)."""

from __future__ import annotations

import argparse
import getpass
import os

from . import security, service
from .db import Store


def main() -> None:
    p = argparse.ArgumentParser(prog="portal.cli")
    sub = p.add_subparsers(dest="command", required=True)
    staff = sub.add_parser("create-staff", help="an administrator or internal user account")
    staff.add_argument("email")
    staff.add_argument("role", choices=["admin", "internal"])
    a = p.parse_args()
    password = getpass.getpass("Password: ")
    problem = security.password_problem(password)
    if problem:
        raise SystemExit(f"password rule: {security.PASSWORD_RULE} ({problem})")
    store = Store(os.environ.get("PORTAL_DB", os.path.join(os.path.dirname(__file__), "..", "data", "portal.sqlite3")))
    service.create_staff(store, email=a.email, name=a.email.split("@")[0], password=password, role=a.role)
    print(f"{a.role} account {a.email} created")


if __name__ == "__main__":
    main()
