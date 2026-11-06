import uuid
from datetime import timezone
from typing import Any

import pytest
from pydantic import ValidationError

from app.dtz_policy import DTZ_RUBRIC_SCHEMA
from app.models import RubricEvidenceBinding, RubricVersion, RuleDefinition, RuleVersion
from app.models.enums import (
    AssessmentFamily,
    QuestionBankSkill,
    RubricVersionStatus,
    RuleCategory,
    RuleEvaluationStatus,
    RuleSeverity,
    RuleStatus,
)
from app.rules.engine import EvaluationContext, RulesEngine
from app.rules.service import DTZ_RULE_PREVIEW_ONLY, RulesService
from app.standard_packs.dtz_governance import (
    _B1_EXPRESSION,
    _OBJECTIVE_EXPRESSION,
    _OBJECTIVE_FACTS,
    _OBJECTIVE_FIXTURE,
    DTZ_EFFECTIVE_FROM,
    DTZ_OBJECTIVE,
    DTZ_OVERALL_B1,
    DtzPreviewRequest,
    _objective_preview,
)
from app.writing_assessment.service import RubricError, RubricService, canonical_checksum


def _rule(code: str, expression: dict[str, Any], category: RuleCategory) -> RuleVersion:
    return RuleVersion(
        definition=RuleDefinition(code=code, category=category),
        version_label="preview",
        expression=expression,
        severity=RuleSeverity.BLOCKING,
        failure_code="TECHNICAL_TEST_CONDITION_NOT_MET",
        failure_message_template="Draft condition not met",
        internal_policy=False,
        test_cases=[],
    )


@pytest.mark.parametrize(
    ("listening_reading", "writing", "speaking", "expected"),
    [
        ("B1", "B1", "B1", RuleEvaluationStatus.PASS),
        ("B1", "A2", "B1", RuleEvaluationStatus.PASS),
        ("A2", "B1", "B1", RuleEvaluationStatus.PASS),
        ("B1", "BELOW_A2", "B1", RuleEvaluationStatus.FAIL),
        ("BELOW_A2", "B1", "B1", RuleEvaluationStatus.FAIL),
        ("B1", "B1", "A2", RuleEvaluationStatus.FAIL),
        ("A2", "A2", "B1", RuleEvaluationStatus.FAIL),
    ],
)
def test_source_rule_candidate_arithmetic(listening_reading, writing, speaking, expected):
    rule = _rule(DTZ_OVERALL_B1, _B1_EXPRESSION, RuleCategory.RESULT)
    result = RulesEngine().evaluate_rule(
        rule.definition,
        rule,
        EvaluationContext(
            facts={
                "result": {
                    "listening_reading": listening_reading,
                    "writing": writing,
                    "speaking": speaking,
                }
            }
        ),
    )
    assert result.status is expected


def test_objective_preview_scores_one_original_item_and_declared_missing_response():
    rule = _rule(DTZ_OBJECTIVE, _OBJECTIVE_EXPRESSION, RuleCategory.SCORING)
    rule.test_cases = [
        {
            "facts": {"score": _OBJECTIVE_FACTS},
            "original_synthetic_fixture": _OBJECTIVE_FIXTURE,
        }
    ]
    correct = _objective_preview(rule, "B")
    assert correct["awarded_points"] == "1"
    assert correct["combined_maximum_points"] == 45
    assert correct["label"] == "ORIGINAL SYNTHETIC PRACTICE CONTENT"
    assert "correct_option_ids" not in correct
    assert _objective_preview(rule, "C")["awarded_points"] == "0"
    unanswered = _objective_preview(rule, None)
    assert unanswered["status"] == "UNANSWERED"
    assert unanswered["awarded_points"] == "0"


def test_candidate_identity_and_raw_scores_are_not_valid_preview_input():
    data = {"expected_pack_checksum": "a" * 64, "as_of": "2026-11-06"}
    preview = DtzPreviewRequest.model_validate(data)
    assert preview.rule_version_id is None and preview.domains.writing is None
    for extra in (
        {"candidate_id": "actual-candidate"},
        {"exam_version_id": "actual-exam"},
        {"domains": {"writing": 91}},
        {"domains": {"raw_score": 91}},
        {"objective_answer": "unknown-option"},
    ):
        with pytest.raises(ValidationError):
            DtzPreviewRequest.model_validate({**data, **extra})


def test_draft_digest_survives_database_timezone_round_trip_and_generic_preview_is_blocked():
    rule = _rule(DTZ_OVERALL_B1, _B1_EXPRESSION, RuleCategory.RESULT)
    rule.effective_from = DTZ_EFFECTIVE_FROM
    checksum = RulesService.checksum(rule)
    rule.effective_from = DTZ_EFFECTIVE_FROM.astimezone(timezone.utc)
    assert RulesService.checksum(rule) == checksum
    with pytest.raises(ValueError, match=DTZ_RULE_PREVIEW_ONLY):
        RulesService.dry_run(rule, {})
    with pytest.raises(ValueError, match=DTZ_RULE_PREVIEW_ONLY):
        RulesService.transition(None, rule, RuleStatus.PUBLISHED)


def test_review_only_rubric_digest_is_independent_of_evidence_load_order():
    rubric = RubricVersion(
        schema_version=DTZ_RUBRIC_SCHEMA,
        assessment_family=AssessmentFamily.LANGUAGE,
        skill=QuestionBankSkill.WRITING,
        evidence_bindings=[
            RubricEvidenceBinding(
                knowledge_document_version_id=uuid.UUID(int=value),
                reference={"metadata_checksum": str(value) * 64},
            )
            for value in (1, 2)
        ],
    )
    checksum = canonical_checksum(RubricService.checksum_payload(rubric))
    rubric.evidence_bindings.reverse()
    assert canonical_checksum(RubricService.checksum_payload(rubric)) == checksum

    rubric.status = RubricVersionStatus.ACTIVE
    with pytest.raises(RubricError, match="DTZ_DRAFT_RUBRIC_REVIEW_REQUIRED"):
        RubricService.assert_executable(rubric)
