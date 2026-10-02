from enum import Enum


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class OrganisationType(str, Enum):
    PRUFAI = "PRUFAI"
    LANGUAGE_SCHOOL = "LANGUAGE_SCHOOL"
    INTEGRATION_PROVIDER = "INTEGRATION_PROVIDER"
    UNIVERSITY = "UNIVERSITY"
    TEST_CENTRE = "TEST_CENTRE"
    TRAINING_PROVIDER = "TRAINING_PROVIDER"
    ENTERPRISE = "ENTERPRISE"
    OTHER = "OTHER"


class OrganisationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class SiteStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class MembershipStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"


class RoleScope(str, Enum):
    PLATFORM = "PLATFORM"
    TENANT = "TENANT"


class PlatformAssignmentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class AuthSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class InvitationStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class KnowledgeAuthorityType(str, Enum):
    SUPRANATIONAL_STANDARD_BODY = "SUPRANATIONAL_STANDARD_BODY"
    FEDERAL_AUTHORITY = "FEDERAL_AUTHORITY"
    PUBLIC_AGENCY = "PUBLIC_AGENCY"
    EXAM_PROVIDER = "EXAM_PROVIDER"
    INTERNAL = "INTERNAL"
    OTHER = "OTHER"


class KnowledgeScope(str, Enum):
    PLATFORM_SHARED = "PLATFORM_SHARED"
    TENANT_PRIVATE = "TENANT_PRIVATE"


class KnowledgeDocumentType(str, Enum):
    STANDARD = "STANDARD"
    REGULATION = "REGULATION"
    EXAM_SPECIFICATION = "EXAM_SPECIFICATION"
    HANDBOOK = "HANDBOOK"
    SCORING_GUIDE = "SCORING_GUIDE"
    RUBRIC = "RUBRIC"
    PRACTICE_MATERIAL = "PRACTICE_MATERIAL"
    QUESTION_CATALOGUE = "QUESTION_CATALOGUE"
    LEGAL_GUIDANCE = "LEGAL_GUIDANCE"
    POLICY = "POLICY"
    PROCEDURE = "PROCEDURE"
    OTHER = "OTHER"


class KnowledgeVersionStatus(str, Enum):
    DRAFT = "DRAFT"
    INGESTING = "INGESTING"
    INGESTED = "INGESTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


class KnowledgeIngestionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ExtractionStatus(str, Enum):
    EXTRACTED = "EXTRACTED"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class LegalReviewStatus(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AssessmentFamily(str, Enum):
    LANGUAGE = "LANGUAGE"
    INTEGRATION_KNOWLEDGE = "INTEGRATION_KNOWLEDGE"


class KnowledgeIndexJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class KnowledgeIndexStatus(str, Enum):
    NOT_INDEXED = "NOT_INDEXED"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


class ActorType(str, Enum):
    HUMAN = "HUMAN"
    AI_AGENT = "AI_AGENT"
    SYSTEM_SERVICE = "SYSTEM_SERVICE"
