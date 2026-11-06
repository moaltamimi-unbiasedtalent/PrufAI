"""Immutable pool versions and Exam Studio form receipts for synthetic LiD practice."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class LidPracticePoolVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lid_practice_pool_versions"
    __table_args__ = (
        UniqueConstraint(
            "practice_pack_version_id",
            "jurisdiction",
            "version_label",
            name="uq_lid_practice_pool_version",
        ),
        CheckConstraint(
            "(scope = 'GENERAL_DE' AND jurisdiction = 'DE') OR "
            "(scope = 'BUNDESLAND_SPECIFIC' AND jurisdiction LIKE 'DE-__')",
            name="ck_lid_practice_pool_scope",
        ),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    practice_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(5), nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LidPracticeForm(Base):
    __tablename__ = "lid_practice_forms"

    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    blueprint_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    general_pool_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lid_practice_pool_versions.id", ondelete="RESTRICT"), nullable=False
    )
    state_pool_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lid_practice_pool_versions.id", ondelete="RESTRICT"), nullable=False
    )
    bundesland: Mapped[str] = mapped_column(String(5), nullable=False)
    selection_version: Mapped[str] = mapped_column(String(48), nullable=False)
    selection_seed: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
