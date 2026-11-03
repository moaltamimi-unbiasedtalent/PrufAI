"""Pure validation checks complement the required real PostgreSQL/MinIO journey."""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.authorization import TenantContext
from app.models.enums import QuestionBankSkill
from app.speaking_assessment.governance import HumanSpeakingService
from app.writing_assessment.api import _speaking_private_access
from app.writing_assessment.service import RubricError


def rubric():
    return SimpleNamespace(
        skill=QuestionBankSkill.SPEAKING,
        ai_assessment_policy={"mode": "HUMAN_ONLY", "enabled": False},
        human_review_policy={"required": True, "separation_of_duties": True},
        criteria=[{"code": "TASK", "max_points": "4", "evidence_mode": "AUDIO"}],
        performance_levels=[{"code": "BASIC"}, {"code": "CLEAR"}],
        evidence_policy={
            "mode": "AUDIO",
            "time_unit": "milliseconds",
            "required": True,
            "minimum_references": 1,
        },
    )


def fixture():
    asset = SimpleNamespace(id=uuid.uuid4(), sha256="a" * 64, duration_ms=1000)
    revision_id = uuid.uuid4()
    entry = {
        "criterion_code": "TASK",
        "level": "CLEAR",
        "points": "3",
        "rationale": "The original recording answers the practice prompt.",
        "evidence": [
            {
                "asset_id": str(asset.id),
                "audio_sha256": asset.sha256,
                "candidate_response_revision_id": str(revision_id),
                "start_ms": 0,
                "end_ms": 1000,
                "time_unit": "milliseconds",
            }
        ],
    }
    return asset, revision_id, entry


def test_audio_criteria_require_exact_primary_audio_and_bounded_millisecond_spans():
    asset, revision_id, entry = fixture()
    rows, values = HumanSpeakingService.validate_audio_entries(
        rubric(), asset, revision_id, [entry]
    )
    assert values == {"TASK": Decimal("3")}
    assert rows[0]["points"] == "3.0000"
    assert rows[0]["evidence"][0]["end_ms"] == 1000
    assert "confidence" not in rows[0]


@pytest.mark.parametrize(
    "patch",
    [
        {"start_ms": -1},
        {"start_ms": True},
        {"start_ms": 1.5},
        {"end_ms": 0},
        {"end_ms": 1001},
        {"end_ms": "1000"},
        {"time_unit": "seconds"},
        {"asset_id": str(uuid.uuid4())},
        {"audio_sha256": "b" * 64},
        {"candidate_response_revision_id": str(uuid.uuid4())},
        {"quote": "fabricated transcript"},
        {"storage_key": "private/object"},
    ],
)
def test_audio_criteria_reject_invalid_spans_wrong_lineage_and_text_or_storage_references(patch):
    asset, revision_id, entry = fixture()
    entry["evidence"][0].update(patch)
    with pytest.raises(RubricError):
        HumanSpeakingService.validate_audio_entries(rubric(), asset, revision_id, [entry])


@pytest.mark.parametrize(
    "patch",
    [
        {"points": "NaN"},
        {"points": "Infinity"},
        {"points": "4.0001"},
        {"points": "-1"},
        {"points": "1.00001"},
        {"level": "UNKNOWN"},
        {"criterion_code": "UNKNOWN"},
        {"rationale": ""},
        {"rationale": "x" * 2001},
        {"evidence": []},
        {"confidence": "HIGH"},
        {"model_name": "invented"},
    ],
)
def test_audio_criteria_reject_invalid_or_ai_attributed_judgments(patch):
    asset, revision_id, entry = fixture()
    entry.update(patch)
    with pytest.raises(RubricError):
        HumanSpeakingService.validate_audio_entries(rubric(), asset, revision_id, [entry])


def test_audio_proposal_requires_exact_criterion_coverage():
    asset, revision_id, entry = fixture()
    for entries in ([], [entry, copy.deepcopy(entry)]):
        with pytest.raises(RubricError, match="cover every rubric criterion exactly once"):
            HumanSpeakingService.validate_audio_entries(rubric(), asset, revision_id, entries)


@pytest.mark.parametrize("mode", [None, "AI_ASSISTED", "TRANSCRIPT", "AUDIO_AND_TRANSCRIPT"])
def test_human_audio_is_explicit_and_never_a_transcription_or_ai_fallback(mode):
    bound = rubric()
    HumanSpeakingService.assert_human_mode(bound)
    bound.ai_assessment_policy["mode"] = mode
    with pytest.raises(RubricError):
        HumanSpeakingService.assert_human_mode(bound)
    bound = rubric()
    bound.criteria[0]["evidence_mode"] = mode
    with pytest.raises(RubricError):
        HumanSpeakingService.assert_human_mode(bound)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "TRANSCRIPT"),
        ("time_unit", "seconds"),
        ("required", False),
        ("minimum_references", 0),
        ("minimum_references", True),
        ("minimum_references", 21),
    ],
)
def test_audio_rubric_requires_governed_audio_evidence_policy(field, value):
    bound = rubric()
    bound.evidence_policy[field] = value
    with pytest.raises(RubricError):
        HumanSpeakingService.assert_human_mode(bound)


@pytest.mark.parametrize(
    "rubric_skill,asset_id,item_skill",
    [
        (QuestionBankSkill.WRITING, uuid.uuid4(), QuestionBankSkill.WRITING),
        (QuestionBankSkill.WRITING, None, QuestionBankSkill.SPEAKING),
        (None, uuid.uuid4(), QuestionBankSkill.SPEAKING),
    ],
)
def test_legacy_audio_cannot_use_a_mistagged_or_missing_rubric_for_platform_access(
    rubric_skill, asset_id, item_skill
):
    context = TenantContext(
        uuid.uuid4(), uuid.uuid4(), None, platform_permissions=frozenset({"subjective.read"})
    )
    session = SimpleNamespace(get=lambda *_args: SimpleNamespace(skill=item_skill))
    bound = SimpleNamespace(skill=rubric_skill) if rubric_skill is not None else None
    version = SimpleNamespace(
        candidate_media_asset_id=asset_id, question_bank_item_version_id=uuid.uuid4()
    )
    with pytest.raises(HTTPException) as failure:
        _speaking_private_access(session, context, bound, version)
    assert failure.value.status_code == 403
