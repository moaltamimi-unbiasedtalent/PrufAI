from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.security import utcnow
from app.config import get_settings
from app.initial_review_authority.trust import (
    Envelope,
    RecoveryError,
    load_trust,
    require_active,
    verify,
    verify_database,
)
from app.models import InitialReviewAuthority, User
from app.models.enums import UserStatus
from app.scoped_source_review.schemas import CreateGrant


def require_exact_initial_grant(
    session: Session, actor: UUID, organisation_id: UUID, payload: CreateGrant
) -> None:
    try:
        if get_settings().environment == "production":
            raise RecoveryError("RECOVERY_DISABLED_IN_PRODUCTION")
        trust = load_trust()
        if trust.organisation_id != organisation_id:
            raise RecoveryError("WRONG_ORGANISATION")
        # A failed connection/identity probe must not poison the API's audit
        # transaction. On success the lock lives until the parent commits.
        with session.begin_nested():
            verify_database(session, trust.target)
            authority = session.get(
                InitialReviewAuthority, 1, with_for_update=True, populate_existing=True
            )
            if authority is None or authority.ended_at is not None:
                raise RecoveryError("INITIAL_AUTHORITY_UNAVAILABLE")
            envelope = Envelope.model_validate(authority.envelope)
            manifest = verify(envelope, trust)
            if (
                authority.manifest_digest != envelope.manifest_digest
                or manifest.action != "ESTABLISH"
                or manifest.beneficiary_id != actor
                or manifest.grant != payload
            ):
                raise RecoveryError("GRANT_OUTSIDE_INITIAL_AUTHORITY")
            for person in (manifest.approver_id, manifest.executor_id):
                user = session.get(
                    User, person, with_for_update={"read": True}, populate_existing=True
                )
                if user is None or user.status != UserStatus.ACTIVE:
                    raise RecoveryError("SIGNER_ACCOUNT_UNAVAILABLE")
            require_active(manifest, utcnow())
    except (OSError, SQLAlchemyError, ValidationError, ValueError, RecoveryError) as exc:
        raise RecoveryError("INITIAL_GRANT_AUTHORITY_REQUIRED") from exc
