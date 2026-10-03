from decimal import Decimal

import pytest

from app.models.enums import (
    AssessmentFamily,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankTaskType,
)
from app.question_bank.schemas import parse_item_content
from app.question_bank.service import item_checksum, parse_safe_import, validate_item_payload


def _mc(**overrides: object) -> dict:
    content = {
        "kind": "MULTIPLE_CHOICE",
        "prompt": "TEST FIXTURE — NOT OFFICIAL MATERIAL: Which answer?",
        "options": [
            {"option_id": "a", "display_text": "A", "order": 0},
            {"option_id": "b", "display_text": "B", "order": 1},
        ],
        "correct_option_ids": ["a"],
    }
    content.update(overrides)
    return content


def _validate(**overrides: object):
    values = {
        "assessment_family": AssessmentFamily.LANGUAGE,
        "exam_family": None,
        "language": "de",
        "cefr_levels": ["B1"],
        "skill": None,
        "task_type": QuestionBankTaskType.MULTIPLE_CHOICE,
        "response_mode": PackResponseMode.SELECT_ONE,
        "media_requirement": PackMediaRequirement.NONE,
        "content": _mc(),
        "scoring_policy": QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        "max_points": Decimal("1.0"),
        "scoring_metadata": {},
        "rights_status": QuestionBankRightsStatus.PRUFAI_ORIGINAL,
        "licence_reference": None,
        "rubric_reference": None,
    }
    values.update(overrides)
    return validate_item_payload(**values)


def test_synthetic_b1_multiple_choice_is_valid() -> None:
    result = _validate()
    assert result.status == "PASS"
    assert len(result.checksum or "") == 64


def test_lid_cannot_carry_cefr_and_language_queries_are_not_widened() -> None:
    result = _validate(
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family="LEBEN_IN_DEUTSCHLAND",
        language=None,
        cefr_levels=["B1"],
    )
    assert result.status == "FAIL"
    assert any(issue.code == "ITEM.LID_CEFR_NOT_ALLOWED" for issue in result.issues)


def test_answer_key_and_schema_are_structural() -> None:
    result = _validate(content=_mc(correct_option_ids=["unknown"]))
    assert result.status == "FAIL"
    assert any(issue.code == "ITEM.INVALID_ANSWER_KEY" for issue in result.issues)
    with pytest.raises(ValueError):
        parse_item_content({"kind": "MULTIPLE_CHOICE", "prompt": "x", "options": []})


def test_rights_and_pack_compatibility_are_blocking() -> None:
    restricted = _validate(rights_status=QuestionBankRightsStatus.UNKNOWN)
    assert restricted.status == "PASS"  # review is allowed; publication gate is lifecycle-level
    licensed = _validate(rights_status=QuestionBankRightsStatus.LICENSED)
    assert licensed.status == "FAIL"
    mismatch = _validate(pack_task={"task_type": "EXTENDED_WRITING"})
    assert any(issue.code == "ITEM.PACK_TASK_TYPE_MISMATCH" for issue in mismatch.issues)


def test_checksum_is_deterministic_and_content_sensitive() -> None:
    first = item_checksum({"content": _mc(), "rights": "ORIGINAL"})
    same = item_checksum({"rights": "ORIGINAL", "content": _mc()})
    changed = item_checksum({"content": _mc(prompt="changed"), "rights": "ORIGINAL"})
    assert first == same
    assert first != changed


def test_constructed_response_requires_non_objective_scoring() -> None:
    result = _validate(
        task_type=QuestionBankTaskType.EXTENDED_WRITING,
        response_mode=PackResponseMode.LONG_TEXT,
        content={"kind": "EXTENDED_WRITING", "prompt": "Write a response."},
    )
    assert result.status == "FAIL"


def test_blocking_rules_cannot_be_overridden_by_item_validation() -> None:
    result = _validate(rule_result={"status": "BLOCKED", "rule_version_id": "rule-1"})
    assert result.status == "FAIL"
    assert any(issue.code == "ITEM.RULE_BLOCKING_FAILURE" for issue in result.issues)


def test_import_is_inert_and_rejects_executable_fields() -> None:
    assert parse_safe_import({"content": _mc()})["status"] == "DRAFT"
    with pytest.raises(ValueError):
        parse_safe_import({"content": _mc(), "python": "__import__('os')"})
