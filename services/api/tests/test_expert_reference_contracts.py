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
