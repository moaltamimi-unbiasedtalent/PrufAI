from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import RuleCategory, RuleEvaluationStatus, RuleSeverity, RuleStatus
from app.rules.engine import (
    EvaluationContext,
    RuleExpressionError,
    RulesEngine,
    validate_expression,
)
from app.rules.service import RulesService


def _rule(expression: dict, *, code: str = "SYNTHETIC.RULE", category=RuleCategory.STRUCTURE):
    definition = SimpleNamespace(id=uuid.uuid4(), code=code, category=category)
    version = SimpleNamespace(
        id=uuid.uuid4(),
        version_label="1",
        expression=expression,
        checksum="a" * 64,
        severity=RuleSeverity.ERROR,
        failure_code="SYNTHETIC.FAIL",
        failure_message_template="failed",
        evidence_bindings=[],
        definition=definition,
        status=RuleStatus.DRAFT,
    )
    return definition, version


def test_structure_timing_count_and_boundary_rules():
    engine = RulesEngine()
    cases = [
        (
            {"op": "EQ", "left": {"fact": "module.required"}, "right": {"value": True}},
            {"module": {"required": True}},
            RuleEvaluationStatus.PASS,
        ),
        (
            {"op": "LTE", "left": {"fact": "module.time_limit_seconds"}, "right": {"value": 60}},
            {"module": {"time_limit_seconds": 60}},
            RuleEvaluationStatus.PASS,
        ),
        (
            {
                "op": "EQ",
                "left": {"op": "COUNT", "arg": {"fact": "task.items"}},
                "right": {"value": 2},
            },
            {"task": {"items": ["a", "b"]}},
            RuleEvaluationStatus.PASS,
        ),
    ]
    for expression, facts, expected in cases:
        definition, version = _rule(expression)
        assert (
            engine.evaluate_rule(definition, version, EvaluationContext(facts)).status is expected
        )


def test_decimal_scoring_and_result_composition():
    expression = {
        "op": "ALL",
        "args": [
            {
                "op": "GTE",
                "left": {"op": "SUM", "arg": {"fact": "score.parts"}},
                "right": {"value": 1.3},
            },
            {
                "op": "ANY",
                "args": [
                    {"op": "EQ", "left": {"fact": "result.band"}, "right": {"value": "B1"}},
                    {"op": "EQ", "left": {"fact": "result.band"}, "right": {"value": "B2"}},
                ],
            },
        ],
    }
    definition, version = _rule(expression, category=RuleCategory.RESULT)
    result = RulesEngine().evaluate_rule(
        definition,
        version,
        EvaluationContext({"score": {"parts": ["0.65", "0.65"]}, "result": {"band": "B1"}}),
    )
    assert result.status is RuleEvaluationStatus.PASS


def test_missing_and_type_mismatch_are_errors_and_existence_is_safe():
    definition, version = _rule(
        {"op": "EQ", "left": {"fact": "module.missing"}, "right": {"value": 1}}
    )
    assert (
        RulesEngine().evaluate_rule(definition, version, EvaluationContext({"module": {}})).status
        is RuleEvaluationStatus.ERROR
    )
    definition, version = _rule(
        {"op": "LT", "left": {"fact": "module.name"}, "right": {"value": 1}}
    )
    assert (
        RulesEngine()
        .evaluate_rule(definition, version, EvaluationContext({"module": {"name": "x"}}))
        .status
        is RuleEvaluationStatus.ERROR
    )
    definition, version = _rule({"op": "NOT_EXISTS", "arg": {"fact": "module.optional"}})
    assert (
        RulesEngine().evaluate_rule(definition, version, EvaluationContext({"module": {}})).status
        is RuleEvaluationStatus.PASS
    )


def test_certification_guard_blocks_even_a_true_rule():
    definition, version = _rule({"op": "value", "value": True}, category=RuleCategory.CERTIFICATION)
    # invalid operator is intentionally not used; a valid true expression is required.
    version.expression = {"op": "EQ", "left": {"value": 1}, "right": {"value": 1}}
    result = RulesEngine().evaluate_rule(
        definition,
        version,
        EvaluationContext({"pack": {"requested_classification": "OFFICIAL_CERTIFICATION"}}),
    )
    assert result.status is RuleEvaluationStatus.FAIL
    assert result.failure_code == "CERTIFICATION.NOT_AUTHORIZED"


def test_dsl_rejects_code_like_paths_and_complexity():
    for expression in [
        {"fact": "__import__.os"},
        {"op": "EXEC", "args": []},
        {"op": "ALL", "args": []},
        {"value": [1] * 101},
    ]:
        with pytest.raises(RuleExpressionError):
            validate_expression(expression)


def test_validation_checksum_export_diff_and_immutability(session):
    definition = RulesService.create_definition(
        session, code="SYNTHETIC.INTERNAL", name="Synthetic", category=RuleCategory.STRUCTURE
    )
    version = RulesService.create_version(
        session,
        definition,
        version_label="1",
        internal_policy=True,
        expression={"op": "EQ", "left": {"fact": "module.required"}, "right": {"value": True}},
        severity=RuleSeverity.ERROR,
        failure_code="SYNTHETIC.FAIL",
        failure_message_template="failed",
        test_cases=[{"facts": {"module": {"required": True}}, "expected_status": "PASS"}],
    )
    validation = RulesService.validate(session, version)
    assert validation.status == "PASS"
    assert len(validation.checksum) == 64
    exported = RulesService.export(version)
    assert exported["engine"]["version"] == "1.0"
    assert RulesService.import_payload(exported)["rule_code"] == "SYNTHETIC.INTERNAL"
    with pytest.raises(ValueError, match="prohibited"):
        RulesService.import_payload({**exported, "eval": "x"})
    version.status = RuleStatus.ACTIVE
    with pytest.raises(ValueError, match="immutable"):
        RulesService.ensure_mutable(version)
