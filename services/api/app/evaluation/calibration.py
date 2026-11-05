"""Pure, pinned descriptive comparisons; no approval or quality authority.

The governance service verifies submitted judgments, provenance and exact stored
version checksums before calling this module. Nothing here reads evidence,
queries a database, invokes AI, approves labels or activates an artifact.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.evaluation.contracts import Contract

METHOD_VERSION = "governed-calibration-v1"
ComparisonKind = Literal[
    "HUMAN_A_VS_HUMAN_B",
    "ADJUDICATED_VS_HUMAN",
    "AI_PROPOSAL_VS_REFERENCE",
    "VERSION_A_VS_B",
]
REFERENCE_CLASSES = {"SYNTHETIC_TEST", "EXPERT_LABELLED"}
_REQUIRED_PINS = {
    "dataset_version_id",
    "dataset_checksum",
    "dataset_split",
    "rubric_version_id",
    "rubric_checksum",
}
_OPTIONAL_PINS = {
    "standard_pack_version_id",
    "standard_pack_checksum",
    "metric_configuration_checksum",
    "left_model_version_id",
    "left_prompt_version_id",
    "right_model_version_id",
    "right_prompt_version_id",
}


def _finite(value: Any) -> Any:
    try:
        valid = value is None or (not isinstance(value, bool) and Decimal(str(value)).is_finite())
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("calibration points must be finite numeric values")
    return value


class CalibrationCriterion(Contract):
    code: str = Field(min_length=1, max_length=120)
    max_points: Decimal = Field(ge=0, le=1_000_000)
    ordinal_scale: list[Decimal] | None = Field(default=None, min_length=2, max_length=100)

    @field_validator("max_points", mode="before")
    @classmethod
    def finite_maximum(cls, value: Any) -> Any:
        return _finite(value)

    @field_validator("ordinal_scale", mode="before")
    @classmethod
    def finite_scale(cls, value: Any) -> Any:
        if isinstance(value, list):
            for point in value:
                _finite(point)
        return value

    @model_validator(mode="after")
    def ordered_scale(self) -> CalibrationCriterion:
        if self.ordinal_scale is not None and (
            self.ordinal_scale != sorted(set(self.ordinal_scale))
            or any(not 0 <= point <= self.max_points for point in self.ordinal_scale)
        ):
            raise ValueError("ordinal scale must contain distinct increasing rubric score options")
        return self


class CalibrationScore(Contract):
    case_id: str = Field(min_length=1, max_length=120)
    criterion_code: str = Field(min_length=1, max_length=120)
    points: Decimal | None

    @field_validator("points", mode="before")
    @classmethod
    def finite_points(cls, value: Any) -> Any:
        return _finite(value)


class CalibrationSide(Contract):
    kind: Literal["HUMAN_JUDGMENT", "ADJUDICATION", "AI_PROPOSAL"]
    version_id: str = Field(min_length=1, max_length=240)
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_class: Literal["HUMAN", "TEST_ONLY", "REAL_PROVIDER"] = "HUMAN"
    scores: list[CalibrationScore] = Field(max_length=5000)

    @model_validator(mode="after")
    def valid_side(self) -> CalibrationSide:
        cells = [(score.case_id, score.criterion_code) for score in self.scores]
        if len(cells) != len(set(cells)):
            raise ValueError("duplicate calibration case/criterion cell")
        if (self.kind == "AI_PROPOSAL") != (self.execution_class != "HUMAN"):
            raise ValueError("AI proposals require their explicit execution class")
        if not self.version_id.strip():
            raise ValueError("calibration requires an exact nonempty version identity")
        return self


def _pins(raw: dict[str, Any], comparison_kind: ComparisonKind) -> dict[str, str]:
    if not _REQUIRED_PINS <= raw.keys() or not raw.keys() <= _REQUIRED_PINS | _OPTIONAL_PINS:
        raise ValueError("calibration requires the exact supported dataset/rubric pins")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 240:
            raise ValueError("calibration pin values must be exact nonempty identities")
        if key.endswith("checksum") and (
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("calibration checksums must be lowercase SHA-256 values")
        result[key] = value
    if result["dataset_split"] not in {
        "TRAIN",
        "DEVELOPMENT",
        "CALIBRATION",
        "VALIDATION",
        "TEST",
        "HELD_OUT",
    }:
        raise ValueError("calibration requires an explicit supported dataset split")
    if (
        comparison_kind == "VERSION_A_VS_B"
        and not {
            "left_model_version_id",
            "left_prompt_version_id",
            "right_model_version_id",
            "right_prompt_version_id",
        }
        <= result.keys()
    ):
        raise ValueError("version comparison requires both exact model and prompt version pins")
    return result


def _kappa(
    pairs: list[tuple[Decimal, Decimal]],
    scale: list[Decimal] | None,
    independent_pair: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": None,
        "sample_size": len(pairs),
        "weighting": "SQUARED_ORDINAL_INDEX_DISTANCE",
        "ordinal_scale": [str(point) for point in scale] if scale is not None else None,
        "observed_disagreement": None,
        "expected_disagreement": None,
        "unavailable_reason": None,
        "uncertainty": "NOT_ESTIMATED",
    }
    if not independent_pair:
        result["unavailable_reason"] = "INDEPENDENT_FIXED_HUMAN_PAIR_NOT_ESTABLISHED"
    elif scale is None:
        result["unavailable_reason"] = "ORDINAL_SCALE_NOT_DECLARED"
    elif len(pairs) < 2:
        result["unavailable_reason"] = "FEWER_THAN_TWO_PAIRED_OBSERVATIONS"
    else:
        # Marginals and differences use exact counts; the common normalization
        # by (K-1)^2 cancels in the observed/expected disagreement ratio.
        indices = {point: index for index, point in enumerate(scale)}
        left = Counter(indices[a] for a, _ in pairs)
        right = Counter(indices[b] for _, b in pairs)
        observed = Decimal(sum((indices[a] - indices[b]) ** 2 for a, b in pairs))
        expected = sum(
            (Decimal((a - b) ** 2 * na * nb) for a, na in left.items() for b, nb in right.items()),
            Decimal(0),
        ) / Decimal(len(pairs))
        result["observed_disagreement"] = str(observed)
        result["expected_disagreement"] = str(expected)
        if expected == 0:
            result["unavailable_reason"] = "DEGENERATE_CHANCE_DISAGREEMENT"
        else:
            result["value"] = float(Decimal(1) - observed / expected)
    return result


def compare_criterion_scores(
    *,
    comparison_kind: ComparisonKind,
    cases: list[str],
    criteria: list[dict[str, Any]],
    left: dict[str, Any],
    right: dict[str, Any],
    pins: dict[str, Any],
    evidence_class: str,
    independent_human_raters: bool = False,
) -> dict[str, Any]:
    """Compare exact, governance-verified scores over the declared population.

    Differences are right minus left. Missing scores remain in the expected
    denominator and are never imputed. This function cannot establish that
    inputs are genuine expert evidence; that authorization belongs to callers.
    """
    allowed_sides = {
        "HUMAN_A_VS_HUMAN_B": ({"HUMAN_JUDGMENT"}, {"HUMAN_JUDGMENT"}),
        "ADJUDICATED_VS_HUMAN": ({"ADJUDICATION"}, {"HUMAN_JUDGMENT"}),
        "AI_PROPOSAL_VS_REFERENCE": ({"AI_PROPOSAL"}, {"HUMAN_JUDGMENT", "ADJUDICATION"}),
        "VERSION_A_VS_B": ({"AI_PROPOSAL"}, {"AI_PROPOSAL"}),
    }
    if comparison_kind not in allowed_sides or evidence_class not in REFERENCE_CLASSES:
        raise ValueError("unsupported calibration comparison or reference evidence class")
    if (
        not 1 <= len(cases) <= 100
        or any(not isinstance(case, str) or not case.strip() or len(case) > 120 for case in cases)
        or len(cases) != len(set(cases))
        or not 1 <= len(criteria) <= 50
    ):
        raise ValueError("calibration requires a bounded unique case and criterion population")
    specifications = [CalibrationCriterion.model_validate(criterion) for criterion in criteria]
    by_code = {criterion.code: criterion for criterion in specifications}
    if len(by_code) != len(specifications):
        raise ValueError("calibration criterion codes must be unique")
    a, b = CalibrationSide.model_validate(left), CalibrationSide.model_validate(right)
    left_kinds, right_kinds = allowed_sides[comparison_kind]
    if a.kind not in left_kinds or b.kind not in right_kinds:
        raise ValueError("comparison side kinds do not match the requested comparison")
    if a.version_id == b.version_id:
        raise ValueError("comparison requires distinct exact side version identities")
    if evidence_class != "SYNTHETIC_TEST" and any(
        side.execution_class == "TEST_ONLY" for side in (a, b)
    ):
        raise ValueError("test-only proposals must remain SYNTHETIC_TEST")
    exact_pins = _pins(pins, comparison_kind)
    scores = []
    for side in (a, b):
        cells: dict[tuple[str, str], Decimal | None] = {}
        for score in side.scores:
            criterion = by_code.get(score.criterion_code)
            if score.case_id not in cases or criterion is None:
                raise ValueError("calibration score lies outside the declared population")
            if score.points is not None and (
                not 0 <= score.points <= criterion.max_points
                or (
                    criterion.ordinal_scale is not None
                    and score.points not in criterion.ordinal_scale
                )
            ):
                raise ValueError("calibration score is not an exact allowed rubric score")
            cells[(score.case_id, score.criterion_code)] = score.points
        scores.append(cells)
    measured = []
    for criterion in specifications:
        paired = []
        differences = []
        missing_left = missing_right = missing_both = 0
        for case_id in cases:
            av, bv = (cells.get((case_id, criterion.code)) for cells in scores)
            missing_left += av is None
            missing_right += bv is None
            missing_both += av is None and bv is None
            if av is not None and bv is not None:
                paired.append((av, bv))
                differences.append(
                    {
                        "case_id": case_id,
                        "difference": str(bv - av),
                        "absolute_difference": str(abs(bv - av)),
                    }
                )
        n = len(paired)
        deltas = [bv - av for av, bv in paired]
        distribution = Counter(deltas)
        measured.append(
            {
                "criterion_code": criterion.code,
                "max_points": str(criterion.max_points),
                "expected_count": len(cases),
                "sample_size": n,
                "missing_left": missing_left,
                "missing_right": missing_right,
                "missing_both": missing_both,
                "missing_either": len(cases) - n,
                "paired_coverage": n / len(cases),
                "left_coverage": (len(cases) - missing_left) / len(cases),
                "right_coverage": (len(cases) - missing_right) / len(cases),
                "agreement_count": sum(delta == 0 for delta in deltas),
                "agreement_rate": sum(delta == 0 for delta in deltas) / n if n else None,
                "signed_mean_difference": float(sum(deltas, Decimal(0)) / n) if n else None,
                "mean_absolute_error": float(sum((abs(delta) for delta in deltas), Decimal(0)) / n)
                if n
                else None,
                "disagreement_distribution": [
                    {
                        "difference": str(delta),
                        "absolute_difference": str(abs(delta)),
                        "count": count,
                        "rate": count / n,
                    }
                    for delta, count in sorted(distribution.items())
                ],
                "score_differences": differences,
                "quadratic_weighted_kappa": _kappa(
                    paired,
                    criterion.ordinal_scale,
                    comparison_kind == "HUMAN_A_VS_HUMAN_B" and independent_human_raters is True,
                ),
            }
        )
    return {
        "method_version": METHOD_VERSION,
        "comparison_kind": comparison_kind,
        "evidence_class": evidence_class,
        "pins": exact_pins,
        "left": a.model_dump(mode="json", exclude={"scores"}),
        "right": b.model_dump(mode="json", exclude={"scores"}),
        "case_count": len(cases),
        "criterion_count": len(specifications),
        "criteria": measured,
        "empirical_thresholds": "UNCONFIGURED",
        "quality_claim": "NOT_ESTABLISHED",
        "uncertainty": {
            "status": "NOT_ESTIMATED",
            "confidence_interval": None,
            "sample_sufficiency": "NOT_ESTABLISHED",
        },
        "limitations": [
            "Descriptive finite-cohort measurements; "
            "no assessment validity, CEFR, fairness or quality pass claim.",
            "Paired complete scores only; missing labels remain explicit and are not imputed.",
            "Differences are right minus left; criterion score scales are not pooled.",
            "Quadratic weights assume equally spaced ordinal category indices "
            "from the declared rubric scale.",
            "Kappa requires the same independent human pair across cases; "
            "adjudication is a dependent comparison.",
            "Two paired observations are a computational minimum, not evidence of sample adequacy.",
            "Uncertainty and generalization require a separately approved study "
            "accounting for dependent evidence.",
        ],
        "production_activation": False,
    }
