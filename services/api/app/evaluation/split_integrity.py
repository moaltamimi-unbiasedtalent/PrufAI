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


def _content(value: dict[str, Any]) -> str | None:
    raw = value.get("audio_sha256") or value.get("text")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return canonical_checksum(" ".join(unicodedata.normalize("NFC", raw).split()))


def guard_import(session: Session, cases: list[dict[str, Any]]) -> None:
    """Normal synthetic imports cannot copy a protected held-out reference."""
    if session.bind and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('evaluation-split',0))")
        )
    references = list(session.scalars(select(EvaluationReference)))
    for case in cases:
        content = _content(case["input"])
        if content and any(
            row.content_checksum == content and _bucket(row.split) != _bucket(case["split"])
            for row in references
        ):
            raise ValueError("EVALUATION_SPLIT_LEAKAGE")


def guard_reference(session: Session, content_checksum: str, split: str) -> None:
    """A reference cannot silently relabel evidence already in another split."""
    for version in session.scalars(select(EvaluationDatasetVersion)):
        if any(
            _content(case["input"]) == content_checksum and _bucket(case["split"]) != _bucket(split)
            for case in version.cases
        ):
            raise ValueError("EVALUATION_SPLIT_LEAKAGE")
