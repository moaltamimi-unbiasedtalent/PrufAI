"""SQLAlchemy persistence models for the identity and governance foundation."""

from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.identity import (
    AuthSession,
    MembershipRole,
    Organisation,
    OrganisationInvitation,
    OrganisationMembership,
    OrganisationSite,
    PasswordResetToken,
    Permission,
    PlatformRoleAssignment,
    Role,
    RolePermission,
    User,
    UserPasswordCredential,
)
from app.models.knowledge import (
    KnowledgeAuthority,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeLegalMetadata,
    KnowledgeSource,
)
from app.models.retrieval import (
    KnowledgeChunk,
    KnowledgeChunkEmbedding,
    KnowledgeIndexJob,
    KnowledgeIndexProfile,
    KnowledgeRerankingProfile,
)
from app.models.standard_pack import (
    StandardPack,
    StandardPackModule,
    StandardPackSection,
    StandardPackSourceBinding,
    StandardPackTaskSpecification,
    StandardPackVersion,
)

__all__ = [
    "AuditEvent",
    "AuthSession",
    "Base",
    "MembershipRole",
    "Organisation",
    "OrganisationInvitation",
    "OrganisationMembership",
    "OrganisationSite",
    "Permission",
    "PlatformRoleAssignment",
    "PasswordResetToken",
    "Role",
    "RolePermission",
    "User",
    "UserPasswordCredential",
    "KnowledgeAuthority",
    "KnowledgeSource",
    "KnowledgeDocumentVersion",
    "KnowledgeLegalMetadata",
    "KnowledgeIngestionJob",
    "KnowledgeDocumentPage",
    "KnowledgeChunk",
    "KnowledgeChunkEmbedding",
    "KnowledgeIndexJob",
    "KnowledgeIndexProfile",
    "KnowledgeRerankingProfile",
    "StandardPack",
    "StandardPackVersion",
    "StandardPackSourceBinding",
    "StandardPackModule",
    "StandardPackSection",
    "StandardPackTaskSpecification",
]
