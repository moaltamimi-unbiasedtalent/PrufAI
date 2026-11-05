"""Protect known reference evidence across the reused dataset registry."""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import EvaluationDatasetVersion
from app.models.expert_evaluation import EvaluationReference
from app.writing_assessment.service import canonical_checksum


def _bucket(split: str) -> str:
    return "VALIDATION" if split == "TEST" else split


def lock_splits(session: Session) -> None:
    """Use one transaction lock before locking any destination dataset row."""
    if session.bind and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('evaluation-split',0))")
        )


def case_split_keys(case: dict[str, Any]) -> set[str]:
    """Identify exact primary evidence and known lineage, independent of the case ID."""
    value = case.get("input", {})
    keys = {
        "content:" + canonical_checksum(" ".join(unicodedata.normalize("NFC", raw).split()))
        for raw in (value.get("audio_sha256"), value.get("text"))
        if isinstance(raw, str) and raw.strip()
    }
    identity = value.get("evidence_identity", {})
    aliases = {
        "candidate_response_revision_id": "response_revision_id",
        "response_revision_id": "response_revision_id",
        "candidate_response_id": "candidate_response_id",
        "candidate_media_asset_id": "candidate_media_asset_id",
        "attempt_id": "attempt_id",
        "recording_session_id": "recording_session_id",
        "speech_transcription_version_id": "speech_transcription_version_id",
        "known_lineage_reference": "lineage",
    }
    for field, namespace in aliases.items():
        identifier = identity.get(field)
        if isinstance(identifier, str) and identifier:
            keys.add(namespace + ":" + canonical_checksum(identifier))
    execution_id = case.get("execution_job_id")
    if isinstance(execution_id, str) and execution_id:
        keys.add("execution:" + canonical_checksum(execution_id))
    return keys


def _reference_keys(reference: EvaluationReference) -> set[str]:
    return case_split_keys({"input": reference.body.get("input", {})}) | {
        "content:" + reference.content_checksum,
        "lineage:" + reference.lineage_checksum,
    }


def _overlap(claims: dict[str, set[str]], keys: set[str], split: str, *, protect_all: bool) -> bool:
    bucket = _bucket(split)
    return any(
        other != bucket
        and (protect_all or "HELD_OUT" in {other, bucket})
        and keys.intersection(claimed)
        for other, claimed in claims.items()
    )


def _check_versions(session: Session, claims: dict[str, set[str]], *, protect_all: bool) -> None:
    for version in session.scalars(select(EvaluationDatasetVersion)):
        origin = version.provenance.get("origin")
        if origin == "DYNAMIC_REGRESSION_CASE" and _overlap(
            claims,
            set(version.provenance["split_keys"]),
            version.provenance["split"],
            protect_all=True,
        ):
            raise ValueError("EVALUATION_SPLIT_LEAKAGE")
        for case in version.cases:
            if _overlap(claims, case_split_keys(case), case["split"], protect_all=protect_all):
                raise ValueError("EVALUATION_SPLIT_LEAKAGE")


def _check_references(session: Session, claims: dict[str, set[str]]) -> None:
    for reference in session.scalars(select(EvaluationReference)):
        if _overlap(claims, _reference_keys(reference), reference.split, protect_all=True):
            raise ValueError("EVALUATION_SPLIT_LEAKAGE")


def guard_import(session: Session, cases: list[dict[str, Any]]) -> None:
    """Protect held-out data and the declared lineage of promoted evidence on import."""
    lock_splits(session)
    claims: dict[str, set[str]] = {}
    for case in cases:
        keys = case_split_keys(case)
        split = _bucket(case["split"])
        if _overlap(claims, keys, split, protect_all=True):
            raise ValueError("EVALUATION_SPLIT_LEAKAGE")
        if keys:
            claims.setdefault(split, set()).update(keys)
    if claims:
        _check_references(session, claims)
        _check_versions(session, claims, protect_all=False)


def guard_reference(
    session: Session,
    content_checksum: str,
    split: str,
    *,
    lineage_checksum: str | None = None,
    input_value: dict[str, Any] | None = None,
) -> None:
    """A reference cannot silently relabel evidence already in another split."""
    lock_splits(session)
    keys = {"content:" + content_checksum}
    if lineage_checksum is not None:
        keys.add("lineage:" + lineage_checksum)
    if input_value is not None:
        keys.update(case_split_keys({"input": input_value}))
    claims = {_bucket(split): keys}
    _check_references(session, claims)
    _check_versions(session, claims, protect_all=True)


def guard_regression(session: Session, keys: set[str], split: str) -> None:
    lock_splits(session)
    if keys:
        claims = {_bucket(split): keys}
        _check_references(session, claims)
        _check_versions(session, claims, protect_all=True)
