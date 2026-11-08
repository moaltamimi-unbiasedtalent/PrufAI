"""Disposable fixtures. No product path provisions privacy authority."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import AuthenticationService
from app.config import Settings
from app.models import OrganisationMembership, User
from app.models.privacy import PrivacyGrant
from app.privacy.service import DISCOVER, INTAKE, READ, REVIEW, VERIFY
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL


def privacy_actors(
    session: Session,
    settings: Settings,
    *,
    organisation_id: UUID,
    subject_id: UUID,
    reviewer_email: str,
    password: str,
) -> PrivacyGrant:
    holder = session.scalar(select(User).where(User.normalized_email == "admin@demo.prufai.local"))
    issuer = session.scalar(select(User).where(User.normalized_email == PLATFORM_ADMIN_EMAIL))
    assert holder is not None and issuer is not None
    reviewer = User(
        email=reviewer_email,
        normalized_email=reviewer_email,
        display_name="TEST ONLY privacy reviewer",
    )
    session.add(reviewer)
    session.flush()
    AuthenticationService(settings).set_password(session, reviewer, password)
    reviewer_membership = OrganisationMembership(
        organisation_id=organisation_id, user_id=reviewer.id
    )
    session.add(reviewer_membership)
    session.flush()
    subject_membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == organisation_id,
            OrganisationMembership.user_id == subject_id,
        )
    )
    intake_membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.organisation_id == organisation_id,
            OrganisationMembership.user_id == holder.id,
        )
    )
    assert subject_membership is not None and intake_membership is not None
    now = datetime.now(UTC)
    grants = []
    for membership, permissions in (
        (intake_membership, [INTAKE, READ]),
        (reviewer_membership, [READ, VERIFY, DISCOVER, REVIEW]),
    ):
        grant = PrivacyGrant(
            organisation_id=organisation_id,
            holder_membership_id=membership.id,
            subject_membership_id=subject_membership.id,
            purpose="ASSESSMENT_DELIVERY",
            capabilities=permissions,
            maturity="TEST_ONLY",
            issued_by=issuer.id,
            authority_receipt=uuid4(),
            starts_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=2),
        )
        session.add(grant)
        grants.append(grant)
    session.commit()
    return grants[-1]
