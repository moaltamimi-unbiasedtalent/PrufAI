"""Provision distinct TEST ONLY privacy grants in the owned browser database."""

import json
import os
from pathlib import Path

from app.config import get_settings
from app.db import create_session_factory
from app.evaluation.disposable import assert_disposable_database
from app.models import User
from app.scripts.seed_demo import PASSWORD, demo_id
from sqlalchemy import select, text
from tests.integration_support.privacy_cases import privacy_actors


def main() -> None:
    settings = get_settings()
    name = assert_disposable_database(settings.database_url)
    if not (
        settings.environment == "development"
        and settings.privacy_test_discovery_enabled
        and os.environ.get("PRUFAI_E2E_ALLOW_DEMO") == "1"
        and "browser" in name
    ):
        raise SystemExit(
            "Privacy grants require a disposable synthetic browser environment"
        )
    with create_session_factory(settings)() as session:
        if session.scalar(text("SELECT current_database()")) != name:
            raise SystemExit("Disposable browser identity mismatch")
        subject = session.scalar(
            select(User).where(User.normalized_email == "history@demo.prufai.local")
        )
        assert subject is not None
        org = demo_id("organisation")
        email = "privacy-reviewer@demo.prufai.local"
        privacy_actors(
            session,
            settings,
            organisation_id=org,
            subject_id=subject.id,
            reviewer_email=email,
            password=os.environ.get("PRUFAI_DEMO_PASSWORD", PASSWORD),
        )
        payload = {
            "organisation": str(org),
            "subject": str(subject.id),
            "reviewer": email,
        }
    path = Path(os.environ["PRUFAI_E2E_PRIVACY_MANIFEST"])
    with os.fdopen(
        os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
    ) as output:
        json.dump(payload, output)


if __name__ == "__main__":
    main()
