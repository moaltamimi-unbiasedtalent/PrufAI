"""Independent original Speaking result prerequisites, never a judged outcome."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Organisation, User

SPEAKING_RESULTS_VERSION_LABEL = "speaking-results-b1-v1"
SPEAKING_RESULTS_EXAM_NAME = (
    "Human-only B1 German Speaking Results Practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL"
)
SPEAKING_RESULTS_CANDIDATE_EMAIL = "speaking.results.candidate@demo.prufai.local"
SPEAKING_RESULTS_PENDING_EMAIL = "speaking.results.pending@demo.prufai.local"


def speaking_results_id(key: str) -> uuid.UUID:
    from app.scripts.seed_audio_demo import audio_id

    return audio_id("speaking-results-" + key)


def reconcile_speaking_results_demo(
    session: Session,
    settings: Settings,
    *,
    enabled: bool,
    organisation: Organisation,
    administrator: User,
    platform_operator: User,
    password: str,
) -> dict[str, str]:
    from app.scripts.seed_audio_demo import _candidate, reconcile_audio_demo

    ids = reconcile_audio_demo(
        session,
        settings,
        enabled=enabled,
        organisation=organisation,
        administrator=administrator,
        platform_operator=platform_operator,
        password=password,
        speaking_results=True,
    )
    pending = _candidate(
        session,
        settings,
        organisation,
        password,
        namespace="speaking-results-pending-",
        email=SPEAKING_RESULTS_PENDING_EMAIL,
        name="Speaking Pending Practice Candidate",
    )
    return {**ids, "speaking_results_pending_candidate_user_id": str(pending.id)}
