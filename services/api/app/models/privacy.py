from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class PrivacyGrant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_grants"
    __table_args__ = (
        Index("ix_privacy_grants_holder", "organisation_id", "holder_membership_id"),
        CheckConstraint("expires_at > starts_at", name="ck_privacy_grant_window"),
        CheckConstraint(
            "holder_membership_id <> subject_membership_id", name="ck_privacy_grant_separation"
        ),
    )

    organisation_id: Mapped[UUID] = mapped_column(ForeignKey("organisations.id"))
    holder_membership_id: Mapped[UUID] = mapped_column(ForeignKey("organisation_memberships.id"))
    subject_membership_id: Mapped[UUID] = mapped_column(ForeignKey("organisation_memberships.id"))
    purpose: Mapped[str] = mapped_column(String(40))
    capabilities: Mapped[list[str]] = mapped_column(JSON)
    authority_receipt: Mapped[UUID] = mapped_column()
    issued_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    maturity: Mapped[str] = mapped_column(String(16))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrivacyCase(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_cases"
    __table_args__ = (
        Index("ix_privacy_case_scope", "organisation_id", "subject_membership_id", "id"),
    )

    organisation_id: Mapped[UUID] = mapped_column(ForeignKey("organisations.id"))
    subject_membership_id: Mapped[UUID] = mapped_column(ForeignKey("organisation_memberships.id"))
    subject_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    request_type: Mapped[str] = mapped_column(String(24))
    purpose: Mapped[str] = mapped_column(String(40))
    categories: Mapped[list[str]] = mapped_column(JSON)
    request_receipt: Mapped[UUID] = mapped_column()
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))


class PrivacyScope(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_scopes"

    case_id: Mapped[UUID] = mapped_column(ForeignKey("privacy_cases.id"), unique=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    verification_receipt: Mapped[UUID] = mapped_column()
    controller_context: Mapped[str] = mapped_column(String(24))
    context_receipt: Mapped[UUID | None] = mapped_column()
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))


class PrivacyPolicyDraft(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_policy_drafts"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id", "purpose", "category", "version", name="uq_privacy_policy_version"
        ),
    )

    organisation_id: Mapped[UUID] = mapped_column(ForeignKey("organisations.id"))
    purpose: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column()
    terms: Mapped[dict[str, Any]] = mapped_column(JSON)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))


class PrivacyHoldReview(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_hold_reviews"
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_privacy_hold_version"),)

    case_id: Mapped[UUID] = mapped_column(ForeignKey("privacy_cases.id"))
    scope_id: Mapped[UUID] = mapped_column(ForeignKey("privacy_scopes.id"))
    version: Mapped[int] = mapped_column()
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[dict[str, Any]] = mapped_column(JSON)
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))


class PrivacyPreview(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "privacy_previews"
    __table_args__ = (Index("ix_privacy_preview_case", "case_id", "created_at"),)

    case_id: Mapped[UUID] = mapped_column(ForeignKey("privacy_cases.id"))
    scope_id: Mapped[UUID] = mapped_column(ForeignKey("privacy_scopes.id"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    grant_id: Mapped[UUID] = mapped_column(ForeignKey("privacy_grants.id"))
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    evidence_checksum: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64))
