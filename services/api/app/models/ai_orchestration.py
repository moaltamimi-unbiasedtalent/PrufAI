from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AIArtifactStatus, AIRunStatus, HumanReviewStatus
from app.models.identity import enum_type


class AIModelProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_model_profiles"
    __table_args__ = (UniqueConstraint("code", name="uq_ai_model_profile_code"),)
    code: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    context_window: Mapped[Optional[int]] = mapped_column(Integer)
    max_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))


class PromptDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompt_definitions"
    code: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    purpose: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    versions: Mapped[list[PromptVersion]] = relationship(
        back_populates="definition", cascade="all, delete-orphan"
    )


class PromptVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_definition_id", "version_label", name="uq_prompt_version"),
    )
    prompt_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    system_template: Mapped[str] = mapped_column(Text, nullable=False)
    instruction_template: Mapped[Optional[str]] = mapped_column(Text)
    input_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[PromptDefinition] = relationship(back_populates="versions")


class AgentDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_definitions"
    code: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    purpose: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    versions: Mapped[list[AgentVersion]] = relationship(
        back_populates="definition", cascade="all, delete-orphan"
    )


class AgentVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_definition_id", "version_label", name="uq_agent_version"),
    )
    agent_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    model_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_model_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="RESTRICT"), nullable=False
    )
    tool_policy: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[AgentDefinition] = relationship(back_populates="versions")
    model_profile: Mapped[AIModelProfile] = relationship()
    prompt_version: Mapped[PromptVersion] = relationship()


class AIWorkflowDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_workflow_definitions"
    code: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    purpose: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    versions: Mapped[list[AIWorkflowVersion]] = relationship(
        back_populates="definition", cascade="all, delete-orphan"
    )


class AIWorkflowVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_definition_id", "version_label", name="uq_workflow_version"),
    )
    workflow_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_workflow_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    graph_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    graph_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[AIArtifactStatus] = mapped_column(enum_type(AIArtifactStatus), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    definition: Mapped[AIWorkflowDefinition] = relationship(back_populates="versions")


class AIRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_status", "status"),)
    workflow_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_workflow_versions.id", ondelete="RESTRICT")
    )
    agent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )
    status: Mapped[AIRunStatus] = mapped_column(enum_type(AIRunStatus), nullable=False)
    request_id: Mapped[str] = mapped_column(String(120), nullable=False)
    organisation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organisations.id"))
    standard_pack_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("standard_pack_versions.id")
    )
    standard_pack_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    initiated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    failure_category: Mapped[Optional[str]] = mapped_column(String(120))


class AIRunStep(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_run_steps"
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_versions.id"))
    model_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_model_profiles.id")
    )
    prompt_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("prompt_versions.id"))
    input_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    output_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[AIRunStatus] = mapped_column(enum_type(AIRunStatus), nullable=False)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    provider_request_id: Mapped[Optional[str]] = mapped_column(String(160))
    error_category: Mapped[Optional[str]] = mapped_column(String(120))
    retrieval_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    rule_evaluation_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class HumanReviewRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "human_review_requests"
    __table_args__ = (Index("ix_human_review_status", "status"),)
    review_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    status: Mapped[HumanReviewStatus] = mapped_column(enum_type(HumanReviewStatus), nullable=False)
    requested_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decision: Mapped[Optional[str]] = mapped_column(String(80))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_checksum: Mapped[Optional[str]] = mapped_column(String(64))
