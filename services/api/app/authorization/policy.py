import uuid
from typing import Optional

from app.authorization.context import TenantContext
from app.authorization.errors import AuthorizationError


class AuthorizationService:
    """Capability-based authorization with explicit platform/tenant scope."""

    @staticmethod
    def has_permission(
        context: TenantContext, permission: str, organisation_id: Optional[uuid.UUID] = None
    ) -> bool:
        target = organisation_id or context.organisation_id
        if permission in context.platform_permissions or (
            f"platform.{permission}" in context.platform_permissions
        ):
            return True
        if target is None or context.organisation_id != target:
            return False
        return permission in context.permissions

    @classmethod
    def require_permission(
        cls, context: TenantContext, permission: str, organisation_id: Optional[uuid.UUID] = None
    ) -> None:
        if not cls.has_permission(context, permission, organisation_id):
            raise AuthorizationError(f"permission denied: {permission}")
