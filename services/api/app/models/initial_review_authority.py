from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InitialReviewAuthority(Base):
    __tablename__ = "initial_review_authority"
    __table_args__ = (CheckConstraint("id = 1", name="ck_initial_review_authority_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    established_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_action: Mapped[str | None] = mapped_column(String(10))
    end_digest: Mapped[str | None] = mapped_column(String(64))
