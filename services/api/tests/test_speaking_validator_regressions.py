"""Legacy validation compatibility; no provider or linguistic quality evidence."""

import uuid

import pytest

from app.speaking_assessment.service import SpeakingAssessmentError, SpeakingAssessmentService


def test_language_and_stage_words_do_not_imply_protected_age_inference():
    SpeakingAssessmentService._reject_protected_inference(
        {"criterion_code": "LANGUAGE", "rationale": "Language use at this stage is described."}
    )
    with pytest.raises(SpeakingAssessmentError, match="protected-characteristic"):
        SpeakingAssessmentService._reject_protected_inference(
            {"criterion_code": "LANGUAGE", "rationale": "The speaker's age is inferred."}
        )


@pytest.mark.parametrize("start,end", [(False, 1), (0, True), (False, True)])
def test_legacy_audio_evidence_requires_actual_integer_milliseconds(start, end):
    asset_id = uuid.uuid4()
    reference = {"candidate_media_asset_id": str(asset_id), "start_ms": start, "end_ms": end}
    with pytest.raises(SpeakingAssessmentError, match="invalid audio evidence bounds"):
        SpeakingAssessmentService.validate_audio_reference(
            reference, asset_id=asset_id, duration_ms=1000
        )
    SpeakingAssessmentService.validate_audio_reference(
        {**reference, "start_ms": 0, "end_ms": 1}, asset_id=asset_id, duration_ms=1000
    )
