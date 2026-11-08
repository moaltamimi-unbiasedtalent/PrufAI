from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, aliased

from app.audit.service import AuditService
from app.auth.api import Principal
from app.authorization import TenantContext
from app.config import Settings
from app.evaluation.disposable import assert_disposable_database
from app.models import MembershipRole, Organisation, OrganisationMembership, Role, User
from app.models.enums import MembershipStatus, OrganisationStatus, RoleScope, UserStatus
from app.models.privacy import (
    PrivacyCase,
    PrivacyGrant,
    PrivacyHoldReview,
    PrivacyPolicyDraft,
    PrivacyPreview,
    PrivacyScope,
)
from app.privacy import discovery
from app.privacy.schemas import (
    CaseIntake,
    HoldReview,
    Operation,
    PolicyDraft,
    PreviewRequest,
    Verification,
)
from app.writing_assessment.service import canonical_checksum

INTAKE = "privacy.case.intake"
READ = "privacy.case.read"
VERIFY = "privacy.subject.verify"
DISCOVER = "privacy.discovery.preview"
REVIEW = "privacy.legal.review"

T = TypeVar("T", PrivacyCase, PrivacyScope, PrivacyPolicyDraft, PrivacyHoldReview, PrivacyPreview)


class PrivacyError(ValueError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.status = status


def denied() -> PrivacyError:
    return PrivacyError("PRIVACY_ACCESS_DENIED", 403)


def require_disposable(session: Session, settings: Settings) -> None:
    """This milestone has no live enrollment or production discovery authority."""
    if (
        not settings.privacy_test_discovery_enabled
        or settings.environment not in {"development", "test"}
        or session.get_bind().dialect.name != "postgresql"
    ):
        raise denied()
    try:
        database = assert_disposable_database(settings.database_url, report=False)
    except ValueError as exc:
        raise denied() from exc
    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    if engine.url != make_url(settings.database_url):
        raise denied()
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    if session.scalar(text("SELECT current_database()")) != database:
        raise denied()


def _grants(
    session: Session,
    principal: Principal,
    org_id: UUID,
    capability: str,
    *,
    subject_id: UUID | None = None,
    subject_membership: UUID | None = None,
    purpose: str | None = None,
) -> list[tuple[PrivacyGrant, UUID]]:
    context = principal.tenant_context
    if not context or context.organisation_id != org_id or context.membership_id is None:
        raise denied()
    holder = aliased(OrganisationMembership)
    subject = aliased(OrganisationMembership)
    subject_user = aliased(User)
    now = datetime.now(UTC)
    query = (
        select(PrivacyGrant, subject.user_id)
        .join(holder, holder.id == PrivacyGrant.holder_membership_id)
        .join(subject, subject.id == PrivacyGrant.subject_membership_id)
        .join(subject_user, subject_user.id == subject.user_id)
        .join(Organisation, Organisation.id == PrivacyGrant.organisation_id)
        .where(
            PrivacyGrant.organisation_id == org_id,
            PrivacyGrant.holder_membership_id == context.membership_id,
            PrivacyGrant.issued_by != principal.user.id,
            PrivacyGrant.issued_by != subject.user_id,
            PrivacyGrant.maturity == "TEST_ONLY",
            PrivacyGrant.starts_at <= now,
            PrivacyGrant.expires_at > now,
            PrivacyGrant.revoked_at.is_(None),
            holder.organisation_id == org_id,
            holder.user_id == principal.user.id,
            holder.status == MembershipStatus.ACTIVE,
            subject.organisation_id == org_id,
            subject.status == MembershipStatus.ACTIVE,
            subject_user.status == UserStatus.ACTIVE,
            Organisation.status == OrganisationStatus.ACTIVE,
            select(MembershipRole.membership_id)
            .join(Role, Role.id == MembershipRole.role_id)
            .where(
                MembershipRole.membership_id == subject.id,
                Role.scope == RoleScope.TENANT,
                Role.key == "EXAMINER",
            )
            .exists(),
        )
    )
    if subject_id is not None:
        query = query.where(subject.user_id == subject_id)
    if subject_membership is not None:
        query = query.where(subject.id == subject_membership)
    if purpose is not None:
        query = query.where(PrivacyGrant.purpose == purpose)
    rows = session.execute(
        query.order_by(PrivacyGrant.id).limit(51).with_for_update(of=PrivacyGrant)
    ).all()
    if len(rows) > 50:
        raise denied()
    return [(grant, user_id) for grant, user_id in rows if capability in grant.capabilities]


def _one_grant(grants: list[tuple[PrivacyGrant, UUID]]) -> PrivacyGrant:
    if len(grants) != 1:
        raise denied()
    return grants[0][0]


def _case(
    session: Session, principal: Principal, org: UUID, case_id: UUID, permission: str
) -> tuple[PrivacyCase, PrivacyGrant]:
    context = principal.tenant_context
    if not context or context.organisation_id != org or context.membership_id is None:
        raise denied()
    case = session.scalar(
        select(PrivacyCase).where(PrivacyCase.id == case_id, PrivacyCase.organisation_id == org)
    )
    if case is None:
        raise denied()
    grant = _one_grant(
        _grants(
            session,
            principal,
            org,
            permission,
            subject_id=case.subject_user_id,
            subject_membership=case.subject_membership_id,
            purpose=case.purpose,
        )
    )
    return case, grant


def audit(
    session: Session,
    principal: Principal,
    event: str,
    case: PrivacyCase | None = None,
    checksum: str | None = None,
) -> None:
    AuditService.record(
        session,
        TenantContext(user_id=principal.user.id, organisation_id=None, membership_id=None),
        event_type=f"privacy.{event}",
        entity_type="privacy_case",
        organisation_id=case.organisation_id if case else None,
        entity_id=str(case.id) if case else None,
        new_state_hash=checksum,
        require_audit_permission=False,
    )


def _operation(session: Session, model: type[T], payload: Operation) -> T | None:
    return session.scalar(select(model).where(model.operation_id == payload.operation_id))


def _scope(session: Session, case: PrivacyCase) -> PrivacyScope | None:
    return session.scalar(select(PrivacyScope).where(PrivacyScope.case_id == case.id))


def _hold(session: Session, case: PrivacyCase) -> PrivacyHoldReview | None:
    return session.scalar(
        select(PrivacyHoldReview)
        .where(PrivacyHoldReview.case_id == case.id)
        .order_by(PrivacyHoldReview.version.desc())
        .limit(1)
    )


def _policy(session: Session, case: PrivacyCase, category: str) -> PrivacyPolicyDraft | None:
    return session.scalar(
        select(PrivacyPolicyDraft)
        .where(
            PrivacyPolicyDraft.organisation_id == case.organisation_id,
            PrivacyPolicyDraft.purpose == case.purpose,
            PrivacyPolicyDraft.category == category,
        )
        .order_by(PrivacyPolicyDraft.version.desc())
        .limit(1)
    )


def _policies(session: Session, case: PrivacyCase) -> dict[str, PrivacyPolicyDraft]:
    result = {}
    for category in case.categories:
        policy = _policy(session, case, category)
        if policy is not None:
            result[category] = policy
    return result


def _summary(case: PrivacyCase, scope: PrivacyScope | None) -> dict[str, Any]:
    return {
        "id": case.id,
        "request_type": case.request_type,
        "purpose": case.purpose,
        "categories": case.categories,
        "case_version": 2 if scope else 1,
        "case_checksum": scope.checksum if scope else case.checksum,
        "scope_checksum": scope.checksum if scope else None,
        "identity": "VERIFIED_TEST_ONLY" if scope else "VERIFICATION_REQUIRED",
        "status": "LEGAL_REVIEW_REQUIRED" if scope else "IDENTITY_VERIFICATION_REQUIRED",
        "controller_context": scope.controller_context if scope else "UNASSESSED",
        "controller_authorization": "NOT_VERIFIED",
        "created_at": case.created_at.astimezone(UTC).isoformat(),
    }


def list_cases(
    session: Session, principal: Principal, org: UUID, after: UUID | None
) -> dict[str, Any]:
    grants = _grants(session, principal, org, READ)
    if not grants:
        raise denied()
    query = (
        select(PrivacyCase, PrivacyScope)
        .outerjoin(PrivacyScope, PrivacyScope.case_id == PrivacyCase.id)
        .where(
            PrivacyCase.organisation_id == org,
            or_(
                *[
                    and_(
                        PrivacyCase.subject_membership_id == grant.subject_membership_id,
                        PrivacyCase.subject_user_id == subject_id,
                        PrivacyCase.purpose == grant.purpose,
                    )
                    for grant, subject_id in grants
                ]
            ),
        )
    )
    if after is not None:
        query = query.where(PrivacyCase.id > after)
    rows = session.execute(query.order_by(PrivacyCase.id).limit(21)).all()
    return {
        "items": [_summary(case, scope) for case, scope in rows[:20]],
        "next": rows[19][0].id if len(rows) > 20 else None,
    }


def detail(session: Session, principal: Principal, org: UUID, case_id: UUID) -> dict[str, Any]:
    case, _ = _case(session, principal, org, case_id, READ)
    policy = _policies(session, case)
    hold = _hold(session, case)
    return {
        **_summary(case, _scope(session, case)),
        "policies": {
            category: {"version": row.version, "checksum": row.checksum, "status": "DRAFT"}
            for category, row in policy.items()
        },
        "hold_checksum": hold.checksum if hold else None,
        "retention": "UNCONFIGURED",
    }


def intake(
    session: Session, principal: Principal, org: UUID, payload: CaseIntake
) -> dict[str, Any]:
    grant = _one_grant(
        _grants(
            session, principal, org, INTAKE, subject_id=payload.subject_id, purpose=payload.purpose
        )
    )
    digest = canonical_checksum(
        [
            str(org),
            str(grant.subject_membership_id),
            str(principal.user.id),
            payload.model_dump(mode="json"),
        ]
    )
    prior = _operation(session, PrivacyCase, payload)
    if prior is not None:
        if prior.organisation_id != org or prior.requester_id != principal.user.id:
            raise denied()
        if prior.checksum != digest:
            raise PrivacyError("OPERATION_CONFLICT")
        return _summary(prior, _scope(session, prior))
    case = PrivacyCase(
        organisation_id=org,
        subject_membership_id=grant.subject_membership_id,
        subject_user_id=payload.subject_id,
        requester_id=principal.user.id,
        request_type=payload.request_type,
        purpose=payload.purpose,
        categories=sorted(payload.categories),
        request_receipt=payload.request_receipt,
        operation_id=payload.operation_id,
        created_at=datetime.now(UTC),
        checksum=digest,
    )
    session.add(case)
    session.flush()
    audit(session, principal, "case_created", case, digest)
    return _summary(case, None)


def verify(
    session: Session, principal: Principal, org: UUID, case_id: UUID, payload: Verification
) -> dict[str, Any]:
    case, _ = _case(session, principal, org, case_id, VERIFY)
    if case.requester_id == principal.user.id:
        raise denied()
    if payload.case_checksum != case.checksum:
        raise PrivacyError("STALE_CASE")
    digest = canonical_checksum(
        [str(case.id), str(principal.user.id), payload.model_dump(mode="json")]
    )
    prior = _operation(session, PrivacyScope, payload)
    if prior is not None:
        if prior.case_id != case_id or prior.reviewer_id != principal.user.id:
            raise denied()
        if prior.checksum != digest:
            raise PrivacyError("OPERATION_CONFLICT")
        return _summary(case, prior)
    if _scope(session, case) is not None:
        raise PrivacyError("SCOPE_ALREADY_VERIFIED")
    scope = PrivacyScope(
        case_id=case.id,
        reviewer_id=principal.user.id,
        verification_receipt=payload.verification_receipt,
        controller_context=payload.controller_context,
        context_receipt=payload.context_receipt,
        operation_id=payload.operation_id,
        created_at=datetime.now(UTC),
        checksum=digest,
    )
    session.add(scope)
    session.flush()
    for event in ("identity_verified_test_only", "scope_verified", "legal_review_required"):
        audit(session, principal, event, case, digest)
    return _summary(case, scope)


def _verified(session: Session, case: PrivacyCase, expected: str) -> PrivacyScope:
    scope = _scope(session, case)
    if scope is None:
        raise PrivacyError("IDENTITY_NOT_VERIFIED")
    if scope.checksum != expected:
        raise PrivacyError("STALE_CASE")
    return scope


def draft_policy(
    session: Session, principal: Principal, org: UUID, case_id: UUID, payload: PolicyDraft
) -> dict[str, Any]:
    case, _ = _case(session, principal, org, case_id, REVIEW)
    scope = _verified(session, case, payload.case_checksum)
    if (
        payload.category not in case.categories
        or payload.controller_context != scope.controller_context
    ):
        raise PrivacyError("SCOPE_MISMATCH")
    terms = payload.model_dump(
        mode="json", exclude={"operation_id", "case_checksum", "previous_checksum"}
    )
    checksum = canonical_checksum(
        [
            str(org),
            str(case.id),
            case.purpose,
            str(principal.user.id),
            payload.previous_checksum,
            terms,
        ]
    )
    prior = _operation(session, PrivacyPolicyDraft, payload)
    current = _policy(session, case, payload.category)
    if prior is not None:
        if prior.organisation_id != org or prior.author_id != principal.user.id:
            raise denied()
        if prior.checksum != checksum or prior != current:
            raise PrivacyError("STALE_POLICY")
        return {"version": prior.version, "checksum": prior.checksum, "status": "DRAFT"}
    if payload.previous_checksum != (current.checksum if current else None):
        raise PrivacyError("STALE_POLICY")
    row = PrivacyPolicyDraft(
        organisation_id=org,
        purpose=case.purpose,
        category=payload.category,
        version=current.version + 1 if current else 1,
        terms=terms,
        author_id=principal.user.id,
        operation_id=payload.operation_id,
        created_at=datetime.now(UTC),
        checksum=checksum,
    )
    session.add(row)
    session.flush()
    audit(session, principal, "policy_draft_created", case, checksum)
    return {"version": row.version, "checksum": checksum, "status": "DRAFT"}


def review_hold(
    session: Session, principal: Principal, org: UUID, case_id: UUID, payload: HoldReview
) -> dict[str, Any]:
    case, _ = _case(session, principal, org, case_id, REVIEW)
    scope = _verified(session, case, payload.case_checksum)
    now = datetime.now(UTC)
    if payload.starts_at > now or payload.review_due_at <= now:
        raise PrivacyError("HOLD_REVIEW_WINDOW_INVALID")
    decision = payload.model_dump(
        mode="json", exclude={"operation_id", "case_checksum", "previous_checksum"}
    )
    checksum = canonical_checksum(
        [str(scope.id), str(principal.user.id), payload.previous_checksum, decision]
    )
    prior = _operation(session, PrivacyHoldReview, payload)
    current = _hold(session, case)
    if prior is not None:
        if prior.case_id != case_id or prior.reviewer_id != principal.user.id:
            raise denied()
        if prior.checksum != checksum or prior != current:
            raise PrivacyError("STALE_HOLD")
        return {
            "version": prior.version,
            "checksum": prior.checksum,
            "status": prior.decision["status"],
        }
    if payload.previous_checksum != (current.checksum if current else None):
        raise PrivacyError("STALE_HOLD")
    row = PrivacyHoldReview(
        case_id=case.id,
        scope_id=scope.id,
        version=current.version + 1 if current else 1,
        reviewer_id=principal.user.id,
        decision=decision,
        operation_id=payload.operation_id,
        created_at=now,
        checksum=checksum,
    )
    session.add(row)
    session.flush()
    audit(session, principal, "hold_reviewed", case, checksum)
    return {"version": row.version, "checksum": checksum, "status": payload.status}


def preview(
    session: Session,
    principal: Principal,
    org: UUID,
    case_id: UUID,
    payload: PreviewRequest,
    settings: Settings,
) -> dict[str, Any]:
    case, grant = _case(session, principal, org, case_id, DISCOVER)
    scope = _verified(session, case, payload.case_checksum)
    if payload.scope_checksum != scope.checksum:
        raise PrivacyError("STALE_SCOPE")
    policies = _policies(session, case)
    if {category: row.checksum for category, row in policies.items()} != payload.policy_pins:
        raise PrivacyError("STALE_POLICY")
    hold = _hold(session, case)
    if payload.hold_checksum != (hold.checksum if hold else None):
        raise PrivacyError("STALE_HOLD")
    previous = _operation(session, PrivacyPreview, payload)
    if previous is not None and (
        previous.case_id != case.id
        or previous.actor_id != principal.user.id
        or previous.grant_id != grant.id
    ):
        raise denied()
    now = datetime.now(UTC)
    hold_state = "HOLD_REVIEW_REQUIRED"
    if hold and datetime.fromisoformat(hold.decision["review_due_at"]) > now:
        hold_state = hold.decision["status"]
    result, fingerprint = discovery.discover(session, case, settings)
    for category in result["categories"]:
        draft = policies.get(category["category"])
        category["retention_policy"] = "UNCONFIGURED"
        category["draft_version"] = draft.version if draft else None
        category["draft_checksum"] = draft.checksum if draft else None
    result.update(
        {
            "case_id": str(case.id),
            "case_version": 2,
            "case_checksum": scope.checksum,
            "subject_scope_version": 1,
            "scope_checksum": scope.checksum,
            "discovery_method": discovery.METHOD,
            "hold": hold_state,
            "hold_checksum": payload.hold_checksum,
            "eligibility": "NOT_DETERMINED",
            "status": "DECISION_REQUIRED",
            "legal_review": "REQUIRED",
            "physical_erasure_authorized": False,
        }
    )
    if datetime.now(UTC) >= grant.expires_at:
        raise denied()
    evidence_checksum = canonical_checksum([fingerprint, result])
    if previous is not None:
        if previous.evidence_checksum != evidence_checksum:
            raise PrivacyError("STALE_PREVIEW")
        return {"id": previous.id, "checksum": previous.checksum, **previous.result}
    result["evaluated_at"] = now.isoformat()
    checksum = canonical_checksum([str(case.id), str(grant.id), str(payload.operation_id), result])
    receipt = PrivacyPreview(
        case_id=case.id,
        scope_id=scope.id,
        actor_id=principal.user.id,
        grant_id=grant.id,
        operation_id=payload.operation_id,
        evidence_checksum=evidence_checksum,
        result=result,
        created_at=now,
        checksum=checksum,
    )
    session.add(receipt)
    session.flush()
    audit(session, principal, "discovery_requested", case, checksum)
    audit(session, principal, "preview_recorded", case, checksum)
    return {"id": receipt.id, "checksum": checksum, **result}
