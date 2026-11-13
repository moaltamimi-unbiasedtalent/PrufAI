"""Expire a single synthetic enrollment receipt on the guarded browser database."""

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import create_session_factory
from app.evaluation.disposable import assert_disposable_database
from app.models import OrganisationInvitation, PasswordResetToken, User
from app.scripts.seed_demo import demo_id
from sqlalchemy import select, text


def main() -> None:
    kind, identifier = sys.argv[1:]
    if os.environ.get("PRUFAI_E2E_ALLOW_DEMO") != "1":
        raise SystemExit("Isolated browser acceptance is required.")
    settings = get_settings()
    name = assert_disposable_database(os.environ.get("DATABASE_URL", ""), report=False)
    if settings.environment != "development" or "browser" not in name:
        raise SystemExit("Isolated development browser database required.")
    assert_disposable_database(settings.database_url, name, report=False)
    with create_session_factory(settings)() as session:
        if session.scalar(text("SELECT current_database()")) != name:
            raise SystemExit("Database identity mismatch.")
        if kind == "invitation":
            receipt = session.get(OrganisationInvitation, uuid.UUID(identifier))
            if (
                receipt is None
                or receipt.organisation_id != demo_id("organisation")
                or not receipt.email_normalized.startswith("enrollment-")
                or not receipt.email_normalized.endswith("@example.test")
            ):
                raise SystemExit("Synthetic invitation required.")
        elif kind == "reset":
            if not identifier.startswith("enrollment-") or not identifier.endswith(
                "@example.test"
            ):
                raise SystemExit("Synthetic account required.")
            receipt = session.scalar(
                select(PasswordResetToken)
                .join(User, PasswordResetToken.user_id == User.id)
                .where(
                    User.normalized_email == identifier,
                    PasswordResetToken.used_at.is_(None),
                )
                .order_by(PasswordResetToken.expires_at.desc())
                .limit(1)
            )
            if receipt is None:
                raise SystemExit("Synthetic reset receipt required.")
        else:
            raise SystemExit("Unexpected enrollment receipt type.")
        receipt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()


if __name__ == "__main__":
    main()
