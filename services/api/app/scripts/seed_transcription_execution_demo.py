"""Isolated technical execution prerequisites, never fabricated recognition.

Only the explicitly invoked development demo seed may create these identities.
The active TEST ONLY profile remains unusable unless the separately guarded
test-adapter setting is enabled. No recording, execution or transcript is seeded.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AIModelProfile, Organisation, User
from app.models.enums import AIArtifactStatus

TRANSCRIPTION_CANDIDATE_EMAIL = "transcription.candidate@demo.prufai.local"
TRANSCRIPTION_EXAM_NAME = (
    "TEST ONLY transcription execution practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL"
)
TRANSCRIPTION_PROFILE_CODE = "prufai.demo.transcription-execution.test-only.v1"
TRANSCRIPTION_PROFILE_NAME = "TEST ONLY deterministic transcription — no speech recognition"
TRANSCRIPTION_PROFILE_CONFIGURATION = {
    "schema_version": "1",
    "adapter_version": "1",
    "languages": ["de"],
    "scenario": "success",
    "delay_seconds": 0,
}
TRANSCRIPTION_POLICY_CONTENT = (
    "SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL\n"
    "Original internal LANGUAGE/de/B1/PRACTICE technical recording workflow.\n"
    "One original private AUDIO item is submitted by an authenticated synthetic candidate.\n"
    "An authorized institution operator may request a durable transcription job.\n"
    "The explicitly enabled TEST ONLY deterministic adapter tests processing and recovery.\n"
    "It performs no speech recognition and establishes no German transcription accuracy.\n"
    "The original recording remains authoritative; derived text supplies no marks or approval.\n"
    "No recording, execution, transcript, assessment, result or release is seeded.\n"
    "No external provider or data transfer is approved by this development fixture.\n"
)


def reconcile_transcription_execution_demo(
    session: Session,
    settings: Settings,
    *,
    enabled: bool,
    organisation: Organisation,
    administrator: User,
    platform_operator: User,
    password: str,
) -> dict[str, str]:
    from app.scripts.seed_audio_demo import audio_id, reconcile_audio_demo

    if settings.environment != "development" or not enabled:
        raise RuntimeError("Transcription demo requires explicit development demo enablement.")
    identifiers = reconcile_audio_demo(
        session,
        settings,
        enabled=enabled,
        organisation=organisation,
        administrator=administrator,
        platform_operator=platform_operator,
        password=password,
        transcription_execution=True,
    )
    identifier = audio_id("transcription-execution-profile")
    profile = session.get(AIModelProfile, identifier)
    by_code = session.scalar(
        select(AIModelProfile).where(AIModelProfile.code == TRANSCRIPTION_PROFILE_CODE)
    )
    if (by_code is not None and by_code.id != identifier) or (
        profile is not None
        and (
            profile.code != TRANSCRIPTION_PROFILE_CODE
            or profile.provider != "DETERMINISTIC_TEST"
            or profile.model_name != "fixture-transcriber"
            or profile.configuration != TRANSCRIPTION_PROFILE_CONFIGURATION
        )
    ):
        raise RuntimeError("Transcription test profile namespace collision; no profile changed.")
    if profile is None:
        profile = AIModelProfile(
            id=identifier,
            code=TRANSCRIPTION_PROFILE_CODE,
            name=TRANSCRIPTION_PROFILE_NAME,
            provider="DETERMINISTIC_TEST",
            model_name="fixture-transcriber",
            purpose="SPEECH_TRANSCRIPTION",
            status=AIArtifactStatus.ACTIVE,
            configuration=dict(TRANSCRIPTION_PROFILE_CONFIGURATION),
            created_by=platform_operator.id,
            approved_by=platform_operator.id,
            approved_at=datetime.now(timezone.utc),
            supports_structured_output=True,
        )
        session.add(profile)
        session.flush()
    if profile.status is not AIArtifactStatus.ACTIVE or profile.approved_by is None:
        raise RuntimeError("Transcription fixture profile approval changed; no approval restored.")
    return {
        **identifiers,
        "transcription_execution_profile_id": str(profile.id),
        "transcription_execution_profile_name": profile.name,
    }
