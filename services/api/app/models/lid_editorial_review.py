"""Immutable decisions attached to existing LiD question versions and review requests."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class LidEditorialReviewReceipt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lid_editorial_review_receipts"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_lid_editorial_review_operation"),
        UniqueConstraint("item_version_id", "action", name="uq_lid_editorial_review_action"),
        CheckConstraint(
            "action IN ('SUBMIT', 'FACTUAL', 'ASSESSMENT', 'RIGHTS')",
            name="ck_lid_editorial_review_action",
        ),
        CheckConstraint(
            "(action = 'SUBMIT' AND decision = 'SUBMIT' "
            "AND evidence_class = 'AUTHENTICATED_SUBMISSION' "
            "AND qualification_id IS NULL AND qualification_checksum IS NULL "
            "AND source_rights_checksum IS NULL AND reason IS NULL) OR "
            "(action <> 'SUBMIT' AND decision IN ('APPROVE', 'REJECT') "
            "AND evidence_class IN ('SYNTHETIC_TEST', 'VERIFIED_CREDENTIAL') "
            "AND qualification_id IS NOT NULL AND qualification_checksum IS NOT NULL "
            "AND reason IS NOT NULL AND "
            "(decision = 'REJECT' OR source_rights_checksum IS NOT NULL))",
            name="ck_lid_editorial_review_evidence",
        ),
    )

    item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"), nullable=False
    )
    review_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("human_review_requests.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str] = mapped_column(String(64), nullable=False)
    item_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    request_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    qualification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_examiner_qualifications.id", ondelete="RESTRICT")
    )
    qualification_checksum: Mapped[str | None] = mapped_column(String(64))
    source_rights_checksum: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    rights: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
