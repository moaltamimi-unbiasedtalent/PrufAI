"""Reject known synthetic provenance before any evidence resolution or data write."""

import uuid

import pytest
from pydantic import ValidationError

from app.evaluation.expert_schemas import SourceInput


@pytest.mark.parametrize(
    "field",
    [
        "source_reference",
        "rights_approval_reference",
        "processing_approval_reference",
        "language_content_review_reference",
    ],
)
@pytest.mark.parametrize("prefix", ["synthetic:", "demo:", "test:", "SyNtHeTiC:"])
def test_genuine_claim_with_known_synthetic_provenance_is_rejected(field, prefix):
    # This is an invalid claim, never a genuine operational source or expert label.
    value = {
        "operation_id": str(uuid.uuid4()),
        "code": "invalid-genuine-claim",
        "organisation_id": str(uuid.uuid4()),
        "kind": "WRITING",
        "split": "CALIBRATION",
        "origin": "APPROVED_CANDIDATE",
        "language": "de",
        "assessment_family": "LANGUAGE",
        "purpose": "EVALUATION_ONLY",
        "retention_classification": "APPROVED_RESTRICTED",
        "source_reference": "source:opaque-review-reference",
        "rights_approval_reference": "rights:opaque-review-reference",
        "processing_approval_reference": "processing:opaque-review-reference",
        "known_lineage_reference": "lineage:opaque-source",
        "standard_pack_version_id": str(uuid.uuid4()),
        "standard_pack_checksum": "a" * 64,
        "rubric_version_id": str(uuid.uuid4()),
        "rubric_checksum": "b" * 64,
        "input": {"language": "de", "modality": "TEXT", "text": "Invalid synthetic claim"},
        field: prefix + "fixture-only",
    }
    with pytest.raises(ValidationError, match="cannot claim synthetic provenance"):
        SourceInput.model_validate(value)


def _adjudication_payload(rationale):
    import uuid

    return {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": "a" * 64,
        "qualification_id": str(uuid.uuid4()),
        "qualification_checksum": "b" * 64,
        "review_reference": "synthetic:unit-adjudication-review",
        "judgment_pins": [
            {"id": str(uuid.uuid4()), "checksum": "c" * 64},
            {"id": str(uuid.uuid4()), "checksum": "d" * 64},
        ],
        "rationale": rationale,
    }


@pytest.mark.parametrize("rationale", [" ", "\t", "\n\r", "\u00a0"])
def test_adjudication_requires_documented_nonblank_rationale(rationale):
    from app.evaluation.expert_requests import AdjudicationInput

    with pytest.raises(ValueError, match="RATIONALE_REQUIRED"):
        AdjudicationInput.model_validate(_adjudication_payload(rationale))


def test_adjudication_normalizes_documented_rationale_without_changing_meaning():
    from app.evaluation.expert_requests import AdjudicationInput

    value = AdjudicationInput.model_validate(
        _adjudication_payload("  SYNTHETIC_TEST documented disagreement rationale.\n")
    )
    assert value.rationale == "SYNTHETIC_TEST documented disagreement rationale."
