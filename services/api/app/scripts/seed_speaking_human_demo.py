"""Original HUMAN_ONLY/AUDIO B1 development prerequisites, never an outcome.

The shared audio scenario builder runs real pack/item/exam governance in a new
namespace. Its rubric remains DRAFT for authenticated independent publication.
No recording, transcript, assessment, AI run, scoring or release is seeded.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import MembershipRole, Organisation, OrganisationMembership, Role, User
from app.models.enums import OrganisationType, RoleScope

SPEAKING_HUMAN_VERSION_LABEL = "human-speaking-b1-v1"
SPEAKING_HUMAN_LABEL = "SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL"
SPEAKING_HUMAN_EXAM_NAME = f"Human-only B1 German Speaking Practice — {SPEAKING_HUMAN_LABEL}"
SPEAKING_HUMAN_CANDIDATE_EMAIL = "speaking.candidate@demo.prufai.local"
SPEAKING_HUMAN_POLICY_CONTENT = (
    f"{SPEAKING_HUMAN_LABEL}\n"
    "Original internal LANGUAGE/de/B1/PRACTICE human Speaking policy.\n"
    "One original ORAL_RESPONSE task pins its private AUDIO recording policy and rubric version.\n"
    "Assessment mode is explicitly HUMAN_ONLY; evidence mode is exclusively AUDIO.\n"
    "TASK and LANGUAGE each permit 0–3 raw practice points, aggregated by SUM.\n"
    "These original descriptors are not official CEFR thresholds "
    "or provider examination material.\n"
    "A real candidate must submit complete-input validated original private audio.\n"
    "An authorized reviewer listens and proposes criterion values with bounded millisecond spans.\n"
    "A different authorized reviewer approves, rejects or requests changes.\n"
    "The rubric begins DRAFT; publication and assessments require authenticated human actions.\n"
    "No transcript, AI execution, result, statement, certification or release is supplied.\n"
    "Explicit original internal policy metadata is a development prerequisite, "
    "not ingestion evidence.\n"
)
SPEAKING_HUMAN_CRITERIA = [
    {
        "code": "TASK",
        "name": "Aufgabe und Verständlichkeit",
        "max_points": "3",
        "weight": "1",
        "evidence_mode": "AUDIO",
        "descriptors": {
            "NOT_YET": "Vorstellung und Hobby sind aus der Aufnahme noch nicht verständlich.",
            "DEVELOPING": "Die Aufnahme vermittelt einige Angaben zur Person und zum Hobby.",
            "CLEAR": "Die Aufnahme nennt einen erfundenen Namen, ein Hobby und einen Grund.",
        },
    },
    {
        "code": "LANGUAGE",
        "name": "Gesprochene Sprache und Zusammenhang",
        "max_points": "3",
        "weight": "1",
        "evidence_mode": "AUDIO",
        "descriptors": {
            "NOT_YET": "Die gesprochenen sprachlichen Mittel vermitteln die Absicht noch nicht.",
            "DEVELOPING": "Einfache gesprochene Sätze vermitteln die Absicht teilweise.",
            "CLEAR": "Verbundene Sätze vermitteln die Vorstellung in der Aufnahme verständlich.",
        },
    },
]
SPEAKING_HUMAN_LEVELS = [
    {"code": "NOT_YET", "name": "Noch nicht erkennbar"},
    {"code": "DEVELOPING", "name": "Teilweise verständlich"},
    {"code": "CLEAR", "name": "Klar verständlich"},
]


def speaking_human_id(key: str) -> uuid.UUID:
    from app.scripts.seed_audio_demo import audio_id

    return audio_id("human-speaking-" + key)


def reconcile_speaking_human_demo(
    session: Session,
    settings: Settings,
    *,
    enabled: bool,
    organisation: Organisation,
    administrator: User,
    platform_operator: User,
    password: str,
) -> dict[str, str]:
    from app.scripts.seed_audio_demo import _candidate, _ensure, reconcile_audio_demo

    ids = reconcile_audio_demo(
        session,
        settings,
        enabled=enabled,
        organisation=organisation,
        administrator=administrator,
        platform_operator=platform_operator,
        password=password,
        human_speaking=True,
    )
    outside = _ensure(
        session,
        Organisation,
        "outside-org",
        namespace="human-speaking-",
        slug="human-speaking-outsider-demo",
        name="Isolated Speaking demonstration institution",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    if outside.slug != "human-speaking-outsider-demo":
        raise RuntimeError("Human Speaking outsider namespace collision; no organisation changed.")
    outsider = _candidate(
        session,
        settings,
        outside,
        password,
        namespace="human-speaking-outside-",
        email="speaking.outsider@demo.prufai.local",
        name="Separate institution reviewer",
    )
    membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == outsider.id,
            OrganisationMembership.organisation_id == outside.id,
        )
    )
    if membership is None:
        raise RuntimeError("Human Speaking outsider membership is unavailable.")
    for key in ("REVIEWER", "EXAM_MANAGER"):
        role = session.scalar(select(Role).where(Role.key == key, Role.scope == RoleScope.TENANT))
        if role is None:
            raise RuntimeError("Human Speaking demo requires existing institution roles.")
        if session.get(MembershipRole, (membership.id, role.id)) is None:
            session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
    session.flush()
    return {
        **ids,
        "speaking_human_outside_org_id": str(outside.id),
        "speaking_human_outside_user_id": str(outsider.id),
    }
