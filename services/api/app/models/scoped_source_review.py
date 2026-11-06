import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class ScopedSourceReviewGrant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scoped_source_review_grants"
    __table_args__ = (
        CheckConstraint("expires_at > starts_at", name="ck_scoped_review_window"),
        CheckConstraint(
            "reviewer_user_id <> authorized_by", name="ck_scoped_review_distinct_issuer"
        ),
        CheckConstraint(
            "status IN ('APPROVED', 'ACTIVE', 'SUSPENDED', 'REVOKED', 'EXPIRED')",
            name="ck_scoped_review_grant_status",
        ),
        Index("ix_scoped_review_reviewer_org", "reviewer_user_id", "organisation_id"),
    )

    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    authorized_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    creation_operation_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    purpose: Mapped[str] = mapped_column(String(240))
    business_approval_reference: Mapped[str] = mapped_column(String(71), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))
    scope_checksum: Mapped[str] = mapped_column(String(64))
    # The grant seals its entire allowlist at creation. No endpoint appends pins.
    resources: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class ScopedSourceReviewOperation(Base):
    __tablename__ = "scoped_source_review_operations"

    operation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scoped_source_review_grants.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(12))
    request_checksum: Mapped[str] = mapped_column(String(64))
