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


class ActorType(str, Enum):
    HUMAN = "HUMAN"
    AI_AGENT = "AI_AGENT"
    SYSTEM_SERVICE = "SYSTEM_SERVICE"
