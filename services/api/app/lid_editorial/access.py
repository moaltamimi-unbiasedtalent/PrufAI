"""Use existing membership and capability checks for private editorial evidence."""

from __future__ import annotations

import os
import uuid

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.auth.api import Principal
from app.authorization import (
    AuthorizationError,
    AuthorizationService,
    TenantContext,
    build_tenant_context,
)
from app.config import get_settings
from app.evaluation.disposable import assert_disposable_database
from app.models.enums import UserStatus
from app.models.identity import User
from app.models.lid_editorial import LidEditorialCollection


def editorial_context(
    session: Session, principal: Principal, organisation_id: uuid.UUID, permission: str
) -> TenantContext:
    old = principal.tenant_context
    if old is not None and old.organisation_id not in {None, organisation_id}:
        raise AuthorizationError("LID_EDITORIAL_ORGANISATION_SCOPE_REQUIRED")
    context = build_tenant_context(session, principal.user.id, organisation_id)
    if context.membership_id is None:
        raise AuthorizationError("LID_EDITORIAL_ACTIVE_MEMBERSHIP_REQUIRED")
    AuthorizationService.require_permission(context, permission, organisation_id)
    return context


def load_collection(
    session: Session, principal: Principal, collection_id: uuid.UUID, permission: str
) -> tuple[LidEditorialCollection, TenantContext]:
    row = session.get(LidEditorialCollection, collection_id)
    if row is None:
        raise ValueError("LID_EDITORIAL_COLLECTION_NOT_FOUND")
    return row, editorial_context(session, principal, row.organisation_id, permission)


def editorial_preview_enabled(session: Session) -> bool:
    """Test decisions and complete draft forms require the actual owned database."""
    if get_settings().environment not in {"development", "test"}:
        return False
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    if not isinstance(engine, Engine):
        return False
    try:
        assert_disposable_database(
            engine.url.render_as_string(hide_password=False),
            os.environ.get("PRUFAI_DISPOSABLE_DATABASE"),
            report=False,
        )
    except ValueError:
        return False
    return True


def editorial_verified_identity(session: Session, actor_id: uuid.UUID) -> bool:
    user = session.get(User, actor_id, populate_existing=True)
    return bool(
        user is not None
        and user.status is UserStatus.ACTIVE
        and not user.normalized_email.endswith((".invalid", ".test", ".local"))
        and "SYNTHETIC" not in (user.display_name or "").upper()
    )


def editorial_verified_reference(reference: str) -> bool:
    return bool(reference and not reference.lower().startswith(("synthetic:", "test:", "demo:")))
