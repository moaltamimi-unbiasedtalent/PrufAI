"""Manually verify, establish or terminate the one-time editorial issuer."""

import argparse
import os
import stat
import sys
import warnings
from getpass import GetPassWarning, getpass
from typing import NoReturn

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.initial_review_authority.recovery import execute
from app.initial_review_authority.trust import (
    Envelope,
    RecoveryError,
    load_trust,
    parse_json,
    read_bounded,
    verify_url,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm-sha256", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if get_settings().environment == "production":
            raise RecoveryError("RECOVERY_DISABLED_IN_PRODUCTION")
        trust = load_trust()
        if os.geteuid() != trust.executor_uid or args.database != trust.target.database:
            raise RecoveryError("UNAUTHORIZED_EXECUTOR_OR_TARGET")
        fd = os.open(args.envelope, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != trust.executor_uid
            or info.st_mode & 0o077
        ):
            os.close(fd)
            raise RecoveryError("UNSAFE_APPROVAL_FILE")
        envelope = Envelope.model_validate(parse_json(read_bounded(fd)))
        if args.confirm_sha256 != envelope.manifest_digest:
            raise RecoveryError("OPERATOR_CONFIRMATION_MISMATCH")
        if not sys.stdin.isatty():
            raise RecoveryError("INTERACTIVE_TERMINAL_REQUIRED")
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            url = make_url(getpass("Approved local PostgreSQL URL: "))
        verify_url(url, trust.target)
        engine = create_engine(url, hide_parameters=True)
        try:
            with Session(engine) as session:
                status = execute(
                    session, trust, envelope, dry_run=args.dry_run, reload_trust=load_trust
                )
        finally:
            engine.dispose()
    except RecoveryError as exc:
        fail(str(exc))
    except (EOFError, GetPassWarning, OSError, SQLAlchemyError, ValidationError, ValueError):
        fail("TRUST_ARTIFACT_OR_DATABASE_UNAVAILABLE")
    print(f"{'DRY_RUN' if args.dry_run else 'APPLIED'} {status} {envelope.manifest_digest}")


def fail(code: str) -> NoReturn:
    raise SystemExit(f"Recovery refused: {code}")


if __name__ == "__main__":
    main()
