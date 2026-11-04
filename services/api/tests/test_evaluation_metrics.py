"""Original synthetic arithmetic and negative observations, never expert calibration."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.contracts import EvaluationCase, validate_case_collection
from app.evaluation.metrics import aggregate_case_results, edit_distance, evaluate_case
from app.evaluation.schemas import DatasetVersionInput


def transcription(text="Guten Tag", actual="Guten Tag"):
    identity = {"audio_sha256": "a" * 64}
    observation = {
        "text": actual,
        "language": "de",
        "modality": "AUDIO",
        "audio_sha256": "a" * 64,
        "evidence_identity": identity,
    }
    case = {
        "case_id": "synthetic-de-1",
        "kind": "TRANSCRIPTION",
        "split": "TEST",
        "input": {
            "language": "de",
            "modality": "AUDIO",
            "audio_kind": "TECHNICAL_TONE",
            "audio_sha256": "a" * 64,
            "duration_ms": 1000,
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "text": text,
            "language": "de",
            "evidence_identity": identity,
        },
        "synthetic_observation": observation,
    }
    return deepcopy(case), deepcopy(observation)


def writing():
    identity = {"response_checksum": "b" * 64}
    observation = {
        "modality": "TEXT",
        "evidence_identity": identity,
        "criteria": [
            {
                "criterion_code": "CONTENT",
                "proposed_points": "3",
                "evidence": [{"start": 0, "end": 5, "quote": "Guten"}],
            }
        ],
    }
    case = {
        "case_id": "synthetic-writing-1",
        "kind": "WRITING",
        "split": "TEST",
        "input": {
            "text": "Guten Tag",
            "language": "de",
            "modality": "TEXT",
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "criteria": [{"code": "CONTENT", "points": "2", "max_points": "4"}],
            "evidence_identity": identity,
        },
        "synthetic_observation": observation,
    }
    return deepcopy(case), deepcopy(observation)


def speaking():
    case, observation = writing()
    identity = {"candidate_media_asset_id": "asset-1", "audio_sha256": "a" * 64}
    case.update(case_id="synthetic-speaking-1", kind="SPEAKING")
    case["input"].update(
        modality="AUDIO",
        audio_kind="TECHNICAL_TONE",
        audio_sha256="a" * 64,
        duration_ms=1000,
        evidence_identity=identity,
    )
    case["reference"]["evidence_identity"] = identity
    observation.update(modality="AUDIO", audio_sha256="a" * 64, evidence_identity=identity)
    observation["criteria"][0]["evidence"] = [
        {
            "type": "AUDIO",
            "candidate_media_asset_id": "asset-1",
            "audio_sha256": "a" * 64,
            "start_ms": 0,
            "end_ms": 500,
        }
    ]
    case["synthetic_observation"] = deepcopy(observation)
    return case, observation


def retrieval():
    identity = {"organisation_id": "org-1", "standard_pack_checksum": "c" * 64}
    source = {
        "id": "source-1",
        "version_id": "version-1",
        "checksum": "d" * 64,
        "organisation_id": "org-1",
        "authorized": True,
        "text": "Original synthetic source: Berlin ist eine Stadt.",
        "relevant": True,
    }
    case = {
        "case_id": "synthetic-retrieval-1",
        "kind": "RETRIEVAL",
        "split": "TEST",
        "input": {
            "text": "Welche Stadt wird genannt?",
            "language": "de",
            "modality": "TEXT",
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "sources": [source],
            "citation_support": [{"source_id": "source-1", "quote": "Berlin", "supported": True}],
            "evidence_identity": identity,
        },
    }
    observation = {
        "evidence_identity": identity,
        "retrieved": [{k: v for k, v in source.items() if k not in {"authorized", "relevant"}}],
        "citations": [
            {
                "source_id": "source-1",
                "version_id": "version-1",
                "checksum": "d" * 64,
                "quote": "Berlin",
            }
        ],
    }
    case["synthetic_observation"] = deepcopy(observation)
    return case, deepcopy(observation)


def measure(case, observation, **kwargs):
    return evaluate_case(case, observation, evidence_class="TECHNICAL_TEST", **kwargs)


def test_wer_cer_arithmetic_with_real_denominators_and_no_quality_claim():
    case, observation = transcription(actual="Guten guten Tag")
    result = measure(case, observation)
    assert result["metrics"]["word_error_rate"]["value"] == 0.5
    assert result["metrics"]["word_error_rate"]["numerator"] == 1
    assert result["metrics"]["word_error_rate"]["denominator"] == 2
    assert result["metrics"]["character_error_rate"]["value"] == pytest.approx(6 / 9)
    assert not result["hard_fail"]
    assert result["metrics"]["word_error_rate"]["evidence_class"] == "TECHNICAL_TEST"
    assert any(
        "not linguistic validity" in s for s in result["metrics"]["word_error_rate"]["limitations"]
    )
    assert edit_distance("kitten", "sitting") == 3


def test_wer_can_exceed_one_and_empty_reference_is_unavailable():
    case, observation = transcription("Hallo", "eins zwei drei")
    assert measure(case, observation)["metrics"]["word_error_rate"]["value"] == 3
    case["reference"]["text"] = ""
    result = measure(case, observation)
    assert result["metrics"]["word_error_rate"]["value"] is None
    assert result["metrics"]["word_error_rate"]["sample_size"] == 0
    assert result["checks"]["empty_output_behavior"] == "FAIL"


def test_missing_reference_does_not_become_perfect_recognition():
    case, observation = transcription()
    case["reference"].pop("text")
    result = measure(case, observation)
    assert result["metrics"]["word_error_rate"]["value"] is None
    assert result["metrics"]["word_error_rate"]["missing_count"] == 1


def test_exact_normalization_is_pinned_and_thresholds_rejected():
    case, observation = transcription("Grüße", "gru\u0308ße")
    plain = measure(case, observation)
    folded = measure(
        case, observation, metric_configuration={"normalization": "NFC_WHITESPACE_CASEFOLD"}
    )
    assert plain["metrics"]["character_error_rate"]["value"] > 0
    assert folded["metrics"]["character_error_rate"]["value"] == 0
    with pytest.raises(ValidationError):
        measure(case, observation, metric_configuration={"minimum_accuracy": 0.8})


@pytest.mark.parametrize(
    "field,value,category",
    [
        ("language", "en", "UNSUPPORTED_LANGUAGE"),
        ("audio_sha256", "b" * 64, "AUDIO_IDENTITY_MISMATCH"),
        ("evidence_identity", {"audio_sha256": "b" * 64}, "EVIDENCE_IDENTITY_MISMATCH"),
        ("actions", ["APPROVE_ASSESSMENT"], "PROMPT_INJECTION_POLICY_VIOLATION"),
        ("tool_calls", ["shell"], "PROMPT_INJECTION_POLICY_VIOLATION"),
        ("approved", True, "PROMPT_INJECTION_POLICY_VIOLATION"),
        ("text", ["malformed"], "INVALID_OUTPUT_SCHEMA"),
    ],
)
def test_deliberate_transcription_and_policy_failures_are_measured(field, value, category):
    case, observation = transcription()
    case["input"]["adversarial"] = True
    observation[field] = value
    result = measure(case, observation)
    assert result["hard_fail"] and category in result["failure_categories"]


def test_empty_transcript_is_a_deletion_not_a_missing_sample():
    case, observation = transcription(actual="")
    result = measure(case, observation)
    assert result["metrics"]["word_error_rate"]["value"] == 1
    assert result["checks"]["empty_output_behavior"] == "FAIL"


def test_segment_timing_and_unsupported_output_are_measured():
    case, observation = transcription()
    observation["segments"] = [{"start_ms": 0, "end_ms": 500}]
    assert "UNSUPPORTED_SEGMENT_OUTPUT" in measure(case, observation)["failure_categories"]
    case["reference"]["segments_supported"] = True
    assert measure(case, observation)["checks"]["segment_timing"] == "PASS"
    observation["segments"][0]["end_ms"] = 1001
    assert "INVALID_SEGMENT_TIMING" in measure(case, observation)["failure_categories"]


def test_writing_criterion_difference_and_missing_reference_are_explicit():
    case, observation = writing()
    result = measure(case, observation)
    assert not result["hard_fail"]
    assert result["metrics"]["mean_absolute_criterion_error"]["value"] == 1
    assert result["criterion_comparisons"][0]["difference"] == "1"
    assert result["metrics"]["mean_human_judgment_range"]["value"] is None
    case["reference"]["criteria"][0].pop("points")
    result = measure(case, observation)
    assert result["metrics"]["mean_absolute_criterion_error"]["value"] is None
    assert result["metrics"]["mean_absolute_criterion_error"]["missing_count"] == 1


@pytest.mark.parametrize("points", ["-1", "5", "NaN", "Infinity", True, {"value": 2}])
def test_out_of_range_and_malformed_points_are_not_accepted(points):
    case, observation = writing()
    observation["criteria"][0]["proposed_points"] = points
    result = measure(case, observation)
    assert "OUT_OF_RANGE_OR_MISSING_SCORE" in result["failure_categories"]
    assert result["metrics"]["mean_absolute_criterion_error"]["value"] is None


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "wrong_quote", "wrong_span"])
def test_writing_negative_coverage_and_quote_checks(change):
    case, observation = writing()
    if change == "missing":
        observation["criteria"] = []
    elif change == "duplicate":
        observation["criteria"] *= 2
    elif change == "unknown":
        observation["criteria"][0]["criterion_code"] = "UNKNOWN"
    elif change == "wrong_quote":
        observation["criteria"][0]["evidence"][0]["quote"] = "forged"
    else:
        observation["criteria"][0]["evidence"][0]["end"] = 999
    result = measure(case, observation)
    assert result["hard_fail"]


def test_human_disagreement_is_arithmetic_not_invented_consensus():
    case, observation = writing()
    case["reference"]["criteria"][0]["judgments"] = [
        {"reviewer_id": "technical-reviewer-1", "points": "1"},
        {"reviewer_id": "technical-reviewer-2", "points": "3"},
    ]
    with pytest.raises(ValidationError, match="synthetic expectations"):
        measure(case, observation)
    # This is an isolated contract/arithmetic fixture, not a persisted expert label.
    case["reference"]["label_kind"] = "ADJUDICATED_EXPERT"
    result = measure(case, observation)
    assert result["metrics"]["mean_human_judgment_range"]["value"] == 2
    assert result["metrics"]["mean_human_judgment_range"]["evidence_class"] == "TECHNICAL_TEST"


@pytest.mark.parametrize("change", ["transcript", "wrong_sha", "wrong_asset", "wrong_span"])
def test_speaking_requires_actual_bound_audio_modality_and_spans(change):
    case, observation = speaking()
    if change == "transcript":
        observation["modality"] = "TRANSCRIPT"
    elif change == "wrong_sha":
        observation["audio_sha256"] = "b" * 64
    elif change == "wrong_asset":
        observation["criteria"][0]["evidence"][0]["candidate_media_asset_id"] = "asset-2"
    else:
        observation["criteria"][0]["evidence"][0]["end_ms"] = 1001
    assert measure(case, observation)["hard_fail"]


def test_tone_speaking_only_proves_technical_span_and_score_checks():
    case, observation = speaking()
    result = measure(case, observation)
    assert not result["hard_fail"]
    assert result["metrics"]["mean_absolute_criterion_error"]["value"] == 1
    assert result["metrics"]["acoustic_assessment_validity"]["value"] is None


def test_retrieval_uses_real_ranking_and_catalog_quote_label_checks():
    case, observation = retrieval()
    result = measure(case, observation)
    assert not result["hard_fail"]
    assert result["metrics"]["recall_at_k"]["value"] == 1
    assert result["checks"]["citation_quote_support"] == "PASS"
    assert result["checks"]["citation_labelled_support"] == "PASS"
    assert "Berlin" not in str(result)
    assert "Original synthetic source" not in str(result)


@pytest.mark.parametrize(
    "change,category",
    [
        ("unknown", "CITATION_OR_SOURCE_VERSION_MISMATCH"),
        ("stale", "CITATION_OR_SOURCE_VERSION_MISMATCH"),
        ("checksum", "CITATION_OR_SOURCE_VERSION_MISMATCH"),
        ("unauthorized", "UNAUTHORIZED_SOURCE_EXPOSURE"),
        ("tenant", "CROSS_TENANT_CONTAMINATION"),
        ("quote", "UNSUPPORTED_CITATION_QUOTE"),
        ("support", "REFERENCE_LABEL_REJECTS_CITATION"),
        ("text", "CITATION_OR_SOURCE_VERSION_MISMATCH"),
    ],
)
def test_retrieval_negative_cases_fail_measured_checks(change, category):
    case, observation = retrieval()
    if change == "unknown":
        observation["retrieved"][0]["id"] = "not-in-approved-catalog"
    elif change == "stale":
        observation["retrieved"][0]["version_id"] = "version-stale"
    elif change == "checksum":
        observation["citations"][0]["checksum"] = "e" * 64
    elif change == "unauthorized":
        case["reference"]["sources"][0]["authorized"] = False
    elif change == "tenant":
        case["reference"]["sources"][0]["organisation_id"] = "org-2"
        observation["retrieved"][0]["organisation_id"] = "org-2"
    elif change == "quote":
        observation["citations"][0]["quote"] = "not present in source"
    elif change == "support":
        case["reference"]["citation_support"][0]["supported"] = False
    else:
        observation["retrieved"][0]["text"] = "forged content with correct IDs"
    result = measure(case, observation)
    assert result["hard_fail"] and category in result["failure_categories"]


def test_missing_relevance_and_support_labels_are_unavailable_not_constant_success():
    case, observation = retrieval()
    case["reference"]["sources"][0]["relevant"] = None
    case["reference"]["citation_support"] = []
    result = measure(case, observation)
    assert result["metrics"]["recall_at_k"]["value"] is None
    assert result["checks"]["citation_labelled_support"] == "NOT_AVAILABLE"


def test_replay_never_elevates_to_real_provider_or_pilot_class():
    case, observation = transcription()
    result = evaluate_case(case, observation, evidence_class="REAL_PROVIDER")
    assert result["evidence_class"] == "TECHNICAL_TEST"
    with pytest.raises(ValueError, match="unestablished"):
        evaluate_case(case, observation, evidence_class="PILOT_CALIBRATED")


def test_aggregate_uses_reference_unit_denominators_and_retains_missingness():
    first, first_observation = transcription("eins zwei", "eins")
    second, second_observation = transcription("eins zwei drei vier", "eins zwei drei")
    missing, missing_observation = transcription()
    missing["reference"].pop("text")
    result = aggregate_case_results(
        [
            measure(first, first_observation),
            measure(second, second_observation),
            measure(missing, missing_observation),
        ]
    )
    assert result["metrics"]["word_error_rate"]["value"] == pytest.approx(2 / 6)
    assert result["metrics"]["word_error_rate"]["missing_count"] == 1
    assert result["metrics"]["word_error_rate"]["sample_size"] == 6
    assert result["empirical_thresholds"] == "NONE"
    assert result["pilot_assessment_validity"] == "NOT_ESTABLISHED"
    with pytest.raises(ValueError):
        aggregate_case_results([])


@pytest.mark.parametrize("key", ["text", "audio", "reference", "job"])
def test_exact_split_leakage_is_rejected(key):
    first, _ = transcription()
    second = deepcopy(first)
    second.update(case_id="different-case", split="TRAIN")
    second["input"]["audio_sha256"] = "b" * 64
    second["reference"].pop("text")
    if key == "text":
        first["input"]["text"] = "Grüße   aus Berlin"
        second["input"]["text"] = "Gru\u0308ße aus\nBerlin"
    elif key == "audio":
        second["input"]["audio_sha256"] = first["input"]["audio_sha256"]
    elif key == "reference":
        second["reference"]["text"] = first["reference"]["text"]
    else:
        first["execution_job_id"] = second["execution_job_id"] = "job-1"
    with pytest.raises(ValueError, match="split leakage"):
        validate_case_collection([first, second])
    second["split"] = first["split"]
    validate_case_collection([first, second])


def test_duplicate_case_ids_and_reference_judgment_impersonation_rejected():
    case, _ = transcription()
    with pytest.raises(ValueError, match="unique"):
        validate_case_collection([case, deepcopy(case)])
    case, _ = writing()
    case["reference"]["label_kind"] = "SINGLE_EXPERT"
    case["reference"]["criteria"][0]["judgments"] = [
        {"reviewer_id": "same-technical-actor", "points": "1"},
        {"reviewer_id": "same-technical-actor", "points": "2"},
    ]
    with pytest.raises(ValidationError, match="distinct"):
        EvaluationCase.model_validate(case)


@pytest.mark.parametrize("change", ["duplicate", "unknown_source"])
def test_ambiguous_or_undeclared_citation_support_labels_are_rejected(change):
    case, _ = retrieval()
    if change == "duplicate":
        duplicate = {**case["reference"]["citation_support"][0], "supported": False}
        case["reference"]["citation_support"].append(duplicate)
    else:
        case["reference"]["citation_support"][0]["source_id"] = "undeclared-source"
    with pytest.raises(ValidationError, match="citation support"):
        EvaluationCase.model_validate(case)


def test_missing_rubric_cannot_produce_vacuous_perfect_quality_gate():
    case, observation = writing()
    case["reference"]["criteria"] = []
    observation["criteria"] = []
    result = measure(case, observation)
    assert result["hard_fail"]
    assert result["checks"]["criterion_coverage"] == "NOT_AVAILABLE"
    assert result["metrics"]["criterion_coverage_rate"]["value"] is None
    assert result["metrics"]["mean_absolute_criterion_error"]["value"] is None


@pytest.mark.parametrize("field", ["input", "reference"])
def test_dataset_language_declaration_cannot_disagree_with_case_metadata(field):
    case, _ = transcription()
    case[field]["language"] = "en"
    value = {
        "code": "synthetic-language-scope",
        "assessment_family": "LANGUAGE",
        "language": "de",
        "cefr_level": "B1",
        "exam_scope": "synthetic",
        "skill": "TRANSCRIPTION",
        "purpose": "TECHNICAL_ARITHMETIC",
        "origin": "ORIGINAL_SYNTHETIC",
        "source_legal_status": "OWNED_ORIGINAL",
        "source_reference": "synthetic:original",
        "label_provenance": "SYNTHETIC_EXPECTATION",
        "expert_review_status": "NOT_EXPERT_REVIEWED",
        "retention_classification": "SYNTHETIC_TEST",
        "cases": [case],
    }
    with pytest.raises(ValidationError):
        DatasetVersionInput.model_validate(value)


def test_malformed_extra_authority_status_is_a_safe_schema_failure():
    case, observation = transcription()
    observation["status"] = ["APPROVED"]
    result = measure(case, observation)
    assert result["hard_fail"] and "INVALID_OUTPUT_SCHEMA" in result["failure_categories"]


def test_original_foundation_fixture_payloads_validate_and_exercise_negative_metrics():
    path = Path(__file__).resolve().parents[1] / "evaluation" / "foundation_v1.json"
    payloads = json.loads(path.read_text(encoding="utf-8"))
    assert {payload["skill"] for payload in payloads} == {
        "TRANSCRIPTION",
        "WRITING",
        "SPEAKING",
        "RETRIEVAL",
    }
    for payload in payloads:
        parsed = DatasetVersionInput.model_validate(payload)
        assert parsed.origin == "ORIGINAL_SYNTHETIC"
        assert parsed.expert_review_status == "NOT_EXPERT_REVIEWED"
        cases = [case.model_dump(mode="json") for case in parsed.cases]
        validate_case_collection(cases)
        assert len(cases) == 2
        results = [measure(case, case["synthetic_observation"]) for case in cases]
        assert not results[0]["hard_fail"]
        assert results[1]["hard_fail"]
        assert aggregate_case_results(results)["hard_fail"]
        assert all(result["evidence_class"] == "TECHNICAL_TEST" for result in results)
        assert all(not case.get("execution_job_id") for case in cases)
        for case in cases:
            for source in case["reference"]["sources"]:
                assert source["checksum"] == hashlib.sha256(source["text"].encode()).hexdigest()
