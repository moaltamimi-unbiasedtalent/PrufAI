from typing import Dict, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Permission, Role, RolePermission
from app.models.enums import RoleScope

PLATFORM_PERMISSIONS = (
    "platform.organisation.read",
    "platform.organisation.manage",
    "platform.site.read",
    "platform.site.manage",
    "platform.membership.read",
    "platform.membership.manage",
    "platform.user.read",
    "platform.user.manage",
    "platform.audit.read",
    "platform.role.assign",
    "standards.authority.read",
    "standards.authority.manage",
    "standards.source.read",
    "standards.source.manage",
    "standards.document.read",
    "standards.document.manage",
    "standards.document.review",
    "standards.evaluation.read",
    "standards.evaluation.run",
    "standard_pack.read",
    "standard_pack.manage",
    "standard_pack.review",
    "standard_pack.publish",
    "rules.read",
    "rules.manage",
    "rules.review",
    "rules.publish",
    "rules.evaluate",
    "ai.read",
    "ai.manage",
    "ai.run",
    "ai.review",
    "memory.read",
    "memory.manage",
    "memory.review",
    "memory.activate",
    "memory.evaluate",
    "question_bank.read",
    "question_bank.manage",
    "question_bank.review",
    "question_bank.publish",
    "question_bank.export",
    "question_bank.import",
    "exam.read",
    "exam.manage",
    "exam.review",
    "exam.publish",
    "exam.activate",
    "exam.export",
    "exam_assignment.read",
    "exam_assignment.manage",
    "scoring.run",
    "result.read",
    "result.review",
    "result.release",
    "result.manage",
)
TENANT_PERMISSIONS = (
    "organisation.read",
    "organisation.manage",
    "site.read",
    "site.manage",
    "membership.read",
    "membership.manage",
    "user.read",
    "audit.read",
    "question_bank.read",
    "question_bank.manage",
    "question_bank.review",
    "question_bank.export",
    "exam.read",
    "exam.manage",
    "exam.review",
    "exam.publish",
    "exam.activate",
    "exam.export",
    "exam_assignment.read",
    "exam_assignment.manage",
    "scoring.run",
    "result.read",
    "result.review",
    "result.release",
    "result.manage",
)

ROLE_DEFINITIONS: Dict[str, Tuple[RoleScope, Tuple[str, ...]]] = {
    "PRUFAI_SUPER_ADMIN": (RoleScope.PLATFORM, PLATFORM_PERMISSIONS),
    "PRUFAI_STANDARDS_ADMIN": (
        RoleScope.PLATFORM,
        (
            "platform.organisation.read",
            "platform.audit.read",
            "standards.authority.read",
            "standards.authority.manage",
            "standards.source.read",
            "standards.source.manage",
            "standards.document.read",
            "standards.document.manage",
            "standards.document.review",
            "standards.evaluation.read",
            "standards.evaluation.run",
            "standard_pack.read",
            "standard_pack.manage",
            "standard_pack.review",
            "standard_pack.publish",
            "rules.read",
            "rules.manage",
            "rules.review",
            "rules.publish",
            "rules.evaluate",
            "ai.read",
            "ai.manage",
            "ai.run",
            "ai.review",
            "memory.read",
            "memory.manage",
            "memory.review",
            "memory.activate",
            "memory.evaluate",
            "question_bank.read",
            "question_bank.manage",
            "question_bank.review",
            "question_bank.publish",
            "question_bank.export",
            "question_bank.import",
            "exam.read",
            "exam.manage",
            "exam.review",
            "exam.publish",
            "exam.activate",
            "exam.export",
        ),
    ),
    "PRUFAI_AI_ADMIN": (RoleScope.PLATFORM, ("platform.organisation.read", "platform.audit.read")),
    "INSTITUTION_ADMIN": (RoleScope.TENANT, TENANT_PERMISSIONS),
    "EXAM_MANAGER": (
        RoleScope.TENANT,
        (
            "organisation.read",
            "site.read",
            "membership.read",
            "question_bank.read",
            "question_bank.manage",
            "question_bank.export",
            "exam.read",
            "exam.manage",
            "exam.export",
            "exam_assignment.read",
            "exam_assignment.manage",
            "scoring.run",
            "result.read",
            "result.review",
            "result.release",
            "result.manage",
        ),
    ),
    "EXAM_AUTHOR": (
        RoleScope.TENANT,
        (
            "organisation.read",
            "question_bank.read",
            "question_bank.manage",
            "exam.read",
            "exam.manage",
        ),
    ),
    "REVIEWER": (
        RoleScope.TENANT,
        (
            "organisation.read",
            "question_bank.read",
            "question_bank.review",
            "exam.read",
            "exam.review",
        ),
    ),
    "EXAMINER": (RoleScope.TENANT, ("organisation.read",)),
    "INVIGILATOR": (RoleScope.TENANT, ("site.read",)),
    "TEACHER": (RoleScope.TENANT, ("site.read",)),
}


def seed_defaults(session: Session) -> None:
    """Create the predefined capability vocabulary and role mappings idempotently."""

    permission_keys = set(PLATFORM_PERMISSIONS) | set(TENANT_PERMISSIONS)
    permissions: Dict[str, Permission] = {}
    for key in sorted(permission_keys):
        permission = session.scalar(select(Permission).where(Permission.key == key))
        if permission is None:
            permission = Permission(
                key=key,
                scope=RoleScope.PLATFORM if key in PLATFORM_PERMISSIONS else RoleScope.TENANT,
            )
            session.add(permission)
            session.flush()
        permissions[key] = permission

    for key, (scope, role_permission_keys) in ROLE_DEFINITIONS.items():
        role = session.scalar(select(Role).where(Role.key == key))
        if role is None:
            role = Role(key=key, scope=scope)
            session.add(role)
            session.flush()
        existing = {assignment.permission_id for assignment in role.permissions}
        for permission_key in role_permission_keys:
            permission = permissions[permission_key]
            if permission.id not in existing:
                session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.commit()


__all__ = ["ROLE_DEFINITIONS", "seed_defaults"]
