"""Exact editorial provenance attached to existing Question Bank and pack versions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class LidEditorialCollection(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lid_editorial_collections"
    __table_args__ = (
        UniqueConstraint("organisation_id", "code", name="uq_lid_editorial_collection_scope"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    blueprint_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class LidEditorialItemVersion(Base):
    __tablename__ = "lid_editorial_item_versions"
    __table_args__ = (
        CheckConstraint("ordinal > 0", name="ck_lid_editorial_ordinal"),
        CheckConstraint(
            "(scope = 'GENERAL_DE' AND jurisdiction = 'DE') OR "
            "(scope = 'BUNDESLAND_SPECIFIC' AND jurisdiction = 'DE-BE')",
            name="ck_lid_editorial_jurisdiction",
        ),
    )

    item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_items.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lid_editorial_collections.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(5), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LidEditorialPublication(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lid_editorial_publications"
    __table_args__ = (
        UniqueConstraint("collection_id", "action", name="uq_lid_editorial_publication_action"),
        CheckConstraint(
            "(action = 'APPROVED' AND exam_version_id IS NULL) OR "
            "(action = 'PUBLISHED' AND exam_version_id IS NOT NULL)",
            name="ck_lid_editorial_publication_action",
        ),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lid_editorial_collections.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), unique=True
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    eligibility_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
