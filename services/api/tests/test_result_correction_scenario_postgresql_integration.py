import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.enums import QuestionBankOriginType
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
    validate_governed_result_correction_scenario,
)

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for the governed correction scenario",
)


def test_governed_result_correction_scenario_is_internally_consistent() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    engine = create_engine(configured_url)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        scenario = build_governed_result_correction_scenario(session)
        validate_governed_result_correction_scenario(session, scenario)
        session.expire_all()
        assert session.get(type(scenario.exam_version), scenario.exam_version.id) is not None
        assert session.get(type(scenario.appeal), scenario.appeal.id) is not None
        assert scenario.exam_version.standard_pack_version_id == scenario.standard_pack_version.id
        assert (
            scenario.exam_version.modules[0]
            .sections[0]
            .tasks[0]
            .item_bindings[0]
            .question_bank_item_version_id
            == scenario.objective_item_version_i1.id
        )
        assert (
            scenario.objective_item_version_i1.item_id == scenario.objective_item_version_i2.item_id
        )
        assert scenario.objective_item_version_i1.origin_type is not None
        assert scenario.objective_item_version_i2.origin_type is not None
        assert (
            scenario.objective_item_version_i1.origin_type == QuestionBankOriginType.HUMAN_AUTHORED
        )
        assert (
            scenario.objective_item_version_i2.origin_type == QuestionBankOriginType.HUMAN_AUTHORED
        )
        assert scenario.objective_item_version_i1.created_at is not None
        assert scenario.objective_item_version_i2.created_at is not None
        assert scenario.attempt.candidate_user_id == scenario.candidate.id
        assert (
            scenario.transcription_t1.candidate_media_asset_id == scenario.candidate_media_asset.id
        )
        assert scenario.transcription_t1.audio_sha256 == scenario.candidate_media_asset.sha256
        assert (
            scenario.speaking_version_sp1.speech_transcription_version_id
            == scenario.transcription_t1.id
        )
        assert (
            scenario.speaking_version_sp1.candidate_media_asset_id
            == scenario.candidate_media_asset.id
        )
        assert scenario.scoring_run_s1.attempt_id == scenario.attempt.id
        assert scenario.result_version_r1.scoring_run_id == scenario.scoring_run_s1.id
        assert scenario.appeal.challenged_result_version_id == scenario.result_version_r1.id
        session.rollback()
    finally:
        session.close()
        engine.dispose()
