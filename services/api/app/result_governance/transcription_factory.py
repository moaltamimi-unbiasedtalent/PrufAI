from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.knowledge.storage import ObjectStorage
from app.models import ResultAppeal, SpeechTranscriptionVersion
from app.speech_transcription.provider import SpeechTranscriptionProvider
from app.speech_transcription.service import SpeechTranscriptionService


@dataclass(frozen=True)
class TranscriptionCorrectionFactory:
    """Invoke the governed transcription service for an immutable successor."""

    storage: ObjectStorage
    provider: SpeechTranscriptionProvider
    profile_id: uuid.UUID
    language: str
    historical_version_id: uuid.UUID

    def __call__(
        self, session: Session, context: TenantContext, appeal: ResultAppeal
    ) -> SpeechTranscriptionVersion:
        historical = session.get(SpeechTranscriptionVersion, self.historical_version_id)
        if historical is None:
            raise ValueError("historical transcript is required")
        return SpeechTranscriptionService.transcribe(
            session,
            asset_id=historical.candidate_media_asset_id,
            profile_id=self.profile_id,
            storage=self.storage,
            provider=self.provider,
            language=self.language,
            context=context,
            operation_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"result-correction:{appeal.id}:transcription"
            ),
        )
