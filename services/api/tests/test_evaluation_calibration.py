"""SYNTHETIC_TEST actors and scores; never examiner or pilot calibration."""

from copy import deepcopy

import pytest

from app.evaluation.calibration import compare_criterion_scores


def payload(left=(0, 1, 2, 3), right=(0, 2, 2, 2)):
    cases = [f"synthetic-case-{index}" for index in range(len(left))]
    return {
        "comparison_kind": "HUMAN_A_VS_HUMAN_B",
        "cases": cases,
        "criteria": [{"code": "CONTENT", "max_points": "3", "ordinal_scale": [0, 1, 2, 3]}],
        "left": {
            "kind": "HUMAN_JUDGMENT",
            "version_id": "synthetic-human-a-judgment-version",
            "checksum": "a" * 64,
            "scores": [
                {"case_id": case, "criterion_code": "CONTENT", "points": point}
                for case, point in zip(cases, left, strict=True)
            ],
        },
        "right": {
            "kind": "HUMAN_JUDGMENT",
            "version_id": "synthetic-human-b-judgment-version",
            "checksum": "b" * 64,
            "scores": [
                {"case_id": case, "criterion_code": "CONTENT", "points": point}
                for case, point in zip(cases, right, strict=True)
            ],
        },
        "pins": {
            "dataset_version_id": "synthetic-dataset-version-1",
            "dataset_checksum": "c" * 64,
            "dataset_split": "CALIBRATION",
            "rubric_version_id": "synthetic-rubric-version-1",
            "rubric_checksum": "d" * 64,
        },
        "evidence_class": "SYNTHETIC_TEST",
        "independent_human_raters": True,
    }


def test_criterion_arithmetic_and_quadratic_weighted_kappa_are_measured():
    data = payload()
    original = deepcopy(data)
    result = compare_criterion_scores(**data)
    assert data == original
    metric = result["criteria"][0]
    assert metric["sample_size"] == metric["expected_count"] == 4
    assert metric["agreement_count"] == 2
    assert metric["agreement_rate"] == 0.5
    assert metric["signed_mean_difference"] == 0
    assert metric["mean_absolute_error"] == 0.5
    assert metric["disagreement_distribution"] == [
        {"difference": "-1", "absolute_difference": "1", "count": 1, "rate": 0.25},
        {"difference": "0", "absolute_difference": "0", "count": 2, "rate": 0.5},
        {"difference": "1", "absolute_difference": "1", "count": 1, "rate": 0.25},
    ]
    kappa = metric["quadratic_weighted_kappa"]
    assert kappa["observed_disagreement"] == "2"
    assert kappa["expected_disagreement"] == "8"
    assert kappa["value"] == 0.75
    assert kappa["unavailable_reason"] is None
    assert result["evidence_class"] == "SYNTHETIC_TEST"
    assert result["quality_claim"] == "NOT_ESTABLISHED"
    assert result["empirical_thresholds"] == "UNCONFIGURED"
    assert result["uncertainty"]["confidence_interval"] is None
    assert result["uncertainty"]["sample_sufficiency"] == "NOT_ESTABLISHED"
    assert result["production_activation"] is False
    assert result["pins"] == data["pins"]
    assert "scores" not in result["left"]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [((0, 1, 2), (0, 1, 2), 1), ((0, 1, 2), (2, 1, 0), -1)],
)
def test_quadratic_weighted_kappa_known_non_degenerate_extremes(left, right, expected):
    result = compare_criterion_scores(**payload(left, right))
    assert result["criteria"][0]["quadratic_weighted_kappa"]["value"] == expected


@pytest.mark.parametrize(
    ("left", "right", "reason"),
    [
        ((0, 0), (0, 0), "DEGENERATE_CHANCE_DISAGREEMENT"),
        ((0,), (1,), "FEWER_THAN_TWO_PAIRED_OBSERVATIONS"),
        ((None, None), (0, 1), "FEWER_THAN_TWO_PAIRED_OBSERVATIONS"),
    ],
)
def test_kappa_undefined_never_fabricates_a_perfect_value(left, right, reason):
    kappa = compare_criterion_scores(**payload(left, right))["criteria"][0][
        "quadratic_weighted_kappa"
    ]
    assert kappa["value"] is None
    assert kappa["unavailable_reason"] == reason


def test_missing_labels_retain_declared_population_and_each_missing_side():
    data = payload((0, None, None, None), (0, 1, None, None))
    data["left"]["scores"].pop()
    data["right"]["scores"].pop()
    data["criteria"].append({"code": "FORM", "max_points": "10"})
    result = compare_criterion_scores(**data)
    content, form = result["criteria"]
    assert content["expected_count"] == 4
    assert content["sample_size"] == 1
    assert content["missing_left"] == 3
    assert content["missing_right"] == 2
    assert content["missing_both"] == 2
    assert content["missing_either"] == 3
    assert content["paired_coverage"] == 0.25
    assert content["left_coverage"] == 0.25
    assert content["right_coverage"] == 0.5
    assert form["expected_count"] == form["missing_both"] == 4
    assert form["sample_size"] == 0
    assert form["agreement_rate"] is None
    assert form["signed_mean_difference"] is None
    assert form["mean_absolute_error"] is None
    assert form["disagreement_distribution"] == []


def test_fractional_points_are_not_binned_or_rounded_for_error_metrics():
    data = payload(("0.1", "0.2"), ("0.2", "0.4"))
    data["criteria"][0].pop("ordinal_scale")
    metric = compare_criterion_scores(**data)["criteria"][0]
    assert metric["mean_absolute_error"] == 0.15
    assert [row["difference"] for row in metric["score_differences"]] == ["0.1", "0.2"]
    assert metric["quadratic_weighted_kappa"]["value"] is None
    assert metric["quadratic_weighted_kappa"]["unavailable_reason"] == "ORDINAL_SCALE_NOT_DECLARED"


def test_unknown_independence_and_adjudication_do_not_claim_inter_rater_agreement():
    data = payload()
    data["independent_human_raters"] = False
    result = compare_criterion_scores(**data)
    assert result["criteria"][0]["quadratic_weighted_kappa"]["value"] is None
    data["comparison_kind"] = "ADJUDICATED_VS_HUMAN"
    data["left"]["kind"] = "ADJUDICATION"
    data["independent_human_raters"] = True
    metric = compare_criterion_scores(**data)["criteria"][0]
    assert metric["mean_absolute_error"] == 0.5
    assert metric["quadratic_weighted_kappa"]["unavailable_reason"] == (
        "INDEPENDENT_FIXED_HUMAN_PAIR_NOT_ESTABLISHED"
    )


def test_ai_proposal_vs_reference_preserves_test_only_provenance():
    data = payload()
    data["comparison_kind"] = "AI_PROPOSAL_VS_REFERENCE"
    data["left"].update(kind="AI_PROPOSAL", execution_class="TEST_ONLY")
    result = compare_criterion_scores(**data)
    assert result["evidence_class"] == "SYNTHETIC_TEST"
    assert result["left"]["execution_class"] == "TEST_ONLY"
    assert result["criteria"][0]["quadratic_weighted_kappa"]["value"] is None
    data["evidence_class"] = "EXPERT_LABELLED"
    with pytest.raises(ValueError, match="must remain SYNTHETIC_TEST"):
        compare_criterion_scores(**data)


def test_model_prompt_version_comparison_requires_both_exact_pins():
    data = payload()
    data["comparison_kind"] = "VERSION_A_VS_B"
    for side in ("left", "right"):
        data[side].update(kind="AI_PROPOSAL", execution_class="TEST_ONLY")
    with pytest.raises(ValueError, match="both exact model and prompt"):
        compare_criterion_scores(**data)
    data["pins"].update(
        left_model_version_id="synthetic-model-a-v1",
        left_prompt_version_id="synthetic-prompt-a-v1",
        right_model_version_id="synthetic-model-b-v2",
        right_prompt_version_id="synthetic-prompt-b-v2",
    )
    result = compare_criterion_scores(**data)
    assert result["pins"]["right_prompt_version_id"] == "synthetic-prompt-b-v2"
    assert result["criteria"][0]["mean_absolute_error"] == 0.5
    assert result["quality_claim"] == "NOT_ESTABLISHED"


@pytest.mark.parametrize("point", [True, "NaN", "Infinity", float("-inf"), -1, 4, "0.5"])
def test_invalid_or_out_of_scale_scores_are_rejected(point):
    data = payload()
    data["left"]["scores"][0]["points"] = point
    with pytest.raises(ValueError):
        compare_criterion_scores(**data)


@pytest.mark.parametrize(
    "scale", [[0], [0, 1, 1], [2, 1, 0], [-1, 0], [0, 4], [False, 1], [0, "NaN"]]
)
def test_invalid_ordinal_scales_are_rejected(scale):
    data = payload()
    data["criteria"][0]["ordinal_scale"] = scale
    with pytest.raises(ValueError):
        compare_criterion_scores(**data)


@pytest.mark.parametrize("field", ["cases", "criteria", "scores"])
def test_duplicate_population_or_cells_are_rejected(field):
    data = payload()
    values = data["left"]["scores"] if field == "scores" else data[field]
    values.append(deepcopy(values[0]))
    with pytest.raises(ValueError):
        compare_criterion_scores(**data)


@pytest.mark.parametrize("field", ["case_id", "criterion_code"])
def test_unknown_case_or_criterion_is_rejected(field):
    data = payload()
    data["left"]["scores"][0][field] = "undeclared"
    with pytest.raises(ValueError, match="outside the declared population"):
        compare_criterion_scores(**data)


def test_same_version_and_unknown_unpinned_or_private_metadata_are_rejected():
    data = payload()
    data["right"]["version_id"] = data["left"]["version_id"]
    with pytest.raises(ValueError, match="distinct exact"):
        compare_criterion_scores(**data)
    for pins in ({}, {**payload()["pins"], "private_rationale": "must not project"}):
        data = payload()
        data["pins"] = pins
        with pytest.raises(ValueError, match="exact supported"):
            compare_criterion_scores(**data)


@pytest.mark.parametrize(
    "key,value", [("dataset_checksum", "bad"), ("rubric_version_id", " "), ("dataset_split", "ANY")]
)
def test_inexact_version_or_split_pins_are_rejected(key, value):
    data = payload()
    data["pins"][key] = value
    with pytest.raises(ValueError):
        compare_criterion_scores(**data)


def test_wrong_side_kinds_and_unclassified_ai_are_rejected():
    data = payload()
    data["left"]["kind"] = "ADJUDICATION"
    with pytest.raises(ValueError, match="side kinds"):
        compare_criterion_scores(**data)
    data["left"]["kind"] = "AI_PROPOSAL"
    data["comparison_kind"] = "AI_PROPOSAL_VS_REFERENCE"
    with pytest.raises(ValueError, match="explicit execution class"):
        compare_criterion_scores(**data)


def test_empirical_pilot_or_unrecognized_class_is_never_inferred():
    data = payload()
    data["evidence_class"] = "PILOT_CALIBRATED"
    with pytest.raises(ValueError, match="unsupported"):
        compare_criterion_scores(**data)
