from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

from app.models.enums import RuleEvaluationStatus

ENGINE_NAME = "prufai_rules_engine"
ENGINE_VERSION = "1.0"
_MISSING = object()
_FACT_PATH = re.compile(r"^(pack|module|section|task|exam|score|result)(?:\.[a-z][a-z0-9_]*)*$")
_OPERATORS = {
    "ALL",
    "ANY",
    "NOT",
    "EQ",
    "NE",
    "LT",
    "LTE",
    "GT",
    "GTE",
    "IN",
    "NOT_IN",
    "EXISTS",
    "NOT_EXISTS",
    "COUNT",
    "SUM",
    "BETWEEN",
}
MAX_DEPTH = 20
MAX_NODES = 100
MAX_LIST_LENGTH = 100
MAX_STRING_LENGTH = 1000


class RuleExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationContext:
    facts: Mapping[str, Any]
    evaluation_context_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    standard_pack_version_id: Optional[str] = None
    standard_pack_checksum: Optional[str] = None


@dataclass(frozen=True)
class RuleTrace:
    operator: str
    path: Optional[str]
    actual_value: Any
    expected_value: Any
    result: Optional[bool]


@dataclass(frozen=True)
class RuleResult:
    rule_definition_id: str
    rule_code: str
    rule_version_id: str
    rule_version_label: str
    rule_checksum: Optional[str]
    category: str
    severity: str
    status: RuleEvaluationStatus
    failure_code: Optional[str]
    message: str
    path: Optional[str]
    actual_value: Any = None
    expected_value: Any = None
    evidence_references: tuple[dict[str, Any], ...] = ()
    trace: tuple[RuleTrace, ...] = ()


@dataclass(frozen=True)
class RuleEvaluationReport:
    evaluation_id: str
    engine_name: str
    engine_version: str
    standard_pack_version_id: Optional[str]
    standard_pack_checksum: Optional[str]
    evaluated_at: datetime
    overall_status: RuleEvaluationStatus
    results: tuple[RuleResult, ...]
    summary: dict[str, int]


def _safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:MAX_LIST_LENGTH]]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value[:MAX_LIST_LENGTH]]
    return "[unsupported]"


def _resolve_fact(path: str, facts: Mapping[str, Any]) -> Any:
    if not isinstance(path, str) or not _FACT_PATH.fullmatch(path):
        raise RuleExpressionError("unsafe or unsupported fact path")
    parts = path.split(".")
    current: Any = facts
    for index, part in enumerate(parts):
        if index == 0 and part not in facts:
            return _MISSING
        if isinstance(current, Mapping):
            current = current.get(part, _MISSING)
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def validate_expression(expression: Any) -> None:
    nodes = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise RuleExpressionError("rule expression exceeds complexity limits")
        if not isinstance(node, Mapping):
            raise RuleExpressionError("expression nodes must be objects")
        if "fact" in node:
            if set(node) != {"fact"}:
                raise RuleExpressionError("fact nodes cannot contain extra fields")
            path = node["fact"]
            if not isinstance(path, str) or not _FACT_PATH.fullmatch(path):
                raise RuleExpressionError("unsafe or unsupported fact path")
            return
        if "value" in node:
            if set(node) != {"value"}:
                raise RuleExpressionError("value nodes cannot contain extra fields")
            value = node["value"]
            if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
                raise RuleExpressionError("string literal exceeds complexity limits")
            if isinstance(value, list) and len(value) > MAX_LIST_LENGTH:
                raise RuleExpressionError("list literal exceeds complexity limits")
            return
        operator = node.get("op")
        if operator not in _OPERATORS:
            raise RuleExpressionError("unknown rule operator")
        if operator in {"ALL", "ANY"}:
            args = node.get("args")
            if not isinstance(args, list) or not args:
                raise RuleExpressionError(f"{operator} requires a non-empty args list")
            for child in args:
                walk(child, depth + 1)
        elif operator == "NOT":
            walk(node.get("arg"), depth + 1)
        elif operator in {"EXISTS", "NOT_EXISTS", "COUNT", "SUM"}:
            walk(node.get("arg"), depth + 1)
        else:
            if operator == "BETWEEN":
                walk(node.get("value"), depth + 1)
                bounds = node.get("bounds")
                if not isinstance(bounds, list) or len(bounds) != 2:
                    raise RuleExpressionError("BETWEEN requires exactly two bounds")
                for bound in bounds:
                    walk(bound, depth + 1)
            else:
                if "left" not in node or "right" not in node:
                    raise RuleExpressionError(f"{operator} requires left and right operands")
                walk(node["left"], depth + 1)
                walk(node["right"], depth + 1)

    walk(expression, 0)


class RulesEngine:
    name = ENGINE_NAME
    version = ENGINE_VERSION

    def evaluate(
        self, rules: list[tuple[Any, Any]], context: EvaluationContext
    ) -> RuleEvaluationReport:
        results: list[RuleResult] = []
        for definition, rule_version in sorted(
            rules,
            key=lambda pair: (getattr(pair[1], "sequence", 0), pair[0].code, pair[1].version_label),
        ):
            results.append(self.evaluate_rule(definition, rule_version, context))
        counts = {
            status.value.lower(): sum(result.status is status for result in results)
            for status in RuleEvaluationStatus
        }
        if counts["error"]:
            overall = RuleEvaluationStatus.ERROR
        elif any(result.status is RuleEvaluationStatus.FAIL for result in results):
            overall = RuleEvaluationStatus.FAIL
        else:
            overall = RuleEvaluationStatus.PASS
        return RuleEvaluationReport(
            evaluation_id=str(uuid.uuid4()),
            engine_name=self.name,
            engine_version=self.version,
            standard_pack_version_id=context.standard_pack_version_id,
            standard_pack_checksum=context.standard_pack_checksum,
            evaluated_at=datetime.now(timezone.utc),
            overall_status=overall,
            results=tuple(results),
            summary=counts,
        )

    def evaluate_rule(
        self, definition: Any, rule_version: Any, context: EvaluationContext
    ) -> RuleResult:
        traces: list[RuleTrace] = []
        try:
            validate_expression(rule_version.expression)
            if (
                context.facts.get("pack", {}).get("requested_classification")
                == "OFFICIAL_CERTIFICATION"
            ):
                return self._result(
                    definition,
                    rule_version,
                    RuleEvaluationStatus.FAIL,
                    "CERTIFICATION.NOT_AUTHORIZED",
                    "OFFICIAL_CERTIFICATION is blocked by platform policy",
                    None,
                    traces,
                )
            value = self._evaluate(rule_version.expression, context.facts, traces)
            if not isinstance(value, bool):
                raise RuleExpressionError("rule expression must resolve to a boolean")
            status = RuleEvaluationStatus.PASS if value else RuleEvaluationStatus.FAIL
            message = "Rule satisfied" if value else rule_version.failure_message_template
            return self._result(
                definition,
                rule_version,
                status,
                None if value else rule_version.failure_code,
                message,
                None,
                traces,
            )
        except (RuleExpressionError, TypeError, InvalidOperation, ValueError) as exc:
            return self._result(
                definition,
                rule_version,
                RuleEvaluationStatus.ERROR,
                "RULE.EVALUATION_ERROR",
                str(exc),
                None,
                traces,
            )

    def _evaluate(self, node: Any, facts: Mapping[str, Any], traces: list[RuleTrace]) -> Any:
        validate_expression(node)
        if "fact" in node:
            value = _resolve_fact(node["fact"], facts)
            if value is _MISSING:
                raise RuleExpressionError(f"required fact is missing: {node['fact']}")
            return value
        if "value" in node:
            return node["value"]
        operator = node["op"]
        if operator in {"EXISTS", "NOT_EXISTS"}:
            fact_node = node["arg"]
            if not isinstance(fact_node, Mapping) or "fact" not in fact_node:
                raise RuleExpressionError(f"{operator} requires a fact operand")
            exists = _resolve_fact(fact_node["fact"], facts) is not _MISSING
            result = exists if operator == "EXISTS" else not exists
            traces.append(RuleTrace(operator, fact_node["fact"], exists, None, result))
            return result
        if operator in {"ALL", "ANY"}:
            values = [self._evaluate(child, facts, traces) for child in node["args"]]
            if not all(isinstance(value, bool) for value in values):
                raise RuleExpressionError(f"{operator} operands must be boolean")
            return all(values) if operator == "ALL" else any(values)
        if operator == "NOT":
            value = self._evaluate(node["arg"], facts, traces)
            if not isinstance(value, bool):
                raise RuleExpressionError("NOT operand must be boolean")
            return not value
        if operator == "COUNT":
            value = self._evaluate(node["arg"], facts, traces)
            if not isinstance(value, (list, tuple)):
                raise RuleExpressionError("COUNT operand must be a list")
            return len(value)
        if operator == "SUM":
            value = self._evaluate(node["arg"], facts, traces)
            if not isinstance(value, (list, tuple)):
                raise RuleExpressionError("SUM operand must be a list")
            total = Decimal("0")
            for item in value:
                if isinstance(item, bool) or not isinstance(item, (int, float, Decimal, str)):
                    raise RuleExpressionError("SUM values must be numeric")
                total += Decimal(str(item))
            return total
        if operator == "BETWEEN":
            value = self._evaluate(node["value"], facts, traces)
            low, high = [self._evaluate(item, facts, traces) for item in node["bounds"]]
            return self._compare(value, low, ">=") and self._compare(value, high, "<=")
        left = self._evaluate(node["left"], facts, traces)
        right = self._evaluate(node["right"], facts, traces)
        if operator in {"IN", "NOT_IN"}:
            if not isinstance(right, (list, tuple, set)):
                raise RuleExpressionError("IN operands require a list")
            result = left in right
            return result if operator == "IN" else not result
        if operator in {"EQ", "NE"}:
            result = left == right
            return result if operator == "EQ" else not result
        return self._compare(
            left, right, {"LT": "<", "LTE": "<=", "GT": ">", "GTE": ">="}[operator]
        )

    @staticmethod
    def _compare(left: Any, right: Any, operator: str) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            raise RuleExpressionError("boolean values cannot be numerically compared")
        if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
            left, right = Decimal(str(left)), Decimal(str(right))
        if type(left) is not type(right) and not (
            isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal))
        ):
            raise RuleExpressionError("comparison operand types do not match")
        comparisons: dict[str, bool] = {
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }
        return comparisons[operator]

    @staticmethod
    def _result(
        definition: Any,
        version: Any,
        status: RuleEvaluationStatus,
        failure_code: Optional[str],
        message: str,
        path: Optional[str],
        traces: list[RuleTrace],
    ) -> RuleResult:
        evidence = tuple(
            {
                "id": str(item.id),
                "document_version_id": str(item.knowledge_document_version_id),
                "document_checksum": item.document_checksum,
            }
            for item in getattr(version, "evidence_bindings", [])
        )
        return RuleResult(
            str(definition.id),
            definition.code,
            str(version.id),
            version.version_label,
            version.checksum,
            definition.category.value,
            version.severity.value,
            status,
            failure_code,
            message,
            path,
            trace=tuple(traces[-20:]),
            evidence_references=evidence,
        )
