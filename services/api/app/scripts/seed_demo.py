"""Create the repeatable, synthetic PrüfAI local walkthrough dataset.

This is deliberately an operator script, not an application startup hook. It refuses
production settings and uses the same migration-backed ORM graph as the governance
integration fixture; no schema creation or authentication bypass is involved.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.auth.service import AuthenticationService
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.db import create_session_factory
from app.models import CandidateExamAssignment, MembershipRole, Organisation, OrganisationMembership, Role, User
from app.models.enums import CandidateAssignmentStatus, RoleScope, UserStatus
from app.services.identity import normalize_email


PASSWORD = "Demo-PrufAI-2026!"
ORG_SLUG = "prufai-demo-academy"
ACCOUNTS = {
    "candidate@demo.prufai.local": ("Demo Candidate", "EXAMINER"),
    "reviewer@demo.prufai.local": ("Demo Reviewer", "EXAM_MANAGER"),
    "admin@demo.prufai.local": ("Demo Institution Admin", "INSTITUTION_ADMIN"),
}


def _set_identity(user: User, email: str, display_name: str) -> None:
    user.email = email
    user.normalized_email = normalize_email(email)
    user.display_name = display_name
    user.status = UserStatus.ACTIVE


def seed() -> bool:
    settings = Settings()
    if settings.environment == "production":
        raise RuntimeError("The local demo seed refuses to run in production.")
    factory = create_session_factory(settings)
    with factory() as session:
        existing = session.scalar(select(Organisation).where(Organisation.slug == ORG_SLUG))
        if existing is not None:
            candidate = session.scalar(select(User).where(User.normalized_email == "candidate@demo.prufai.local"))
            template = session.scalar(select(CandidateExamAssignment).where(CandidateExamAssignment.organisation_id == existing.id))
            if candidate is not None and template is not None and session.scalar(select(CandidateExamAssignment).where(CandidateExamAssignment.organisation_id == existing.id, CandidateExamAssignment.candidate_user_id == candidate.id)) is None:
                session.add(CandidateExamAssignment(organisation_id=existing.id, candidate_user_id=candidate.id, exam_version_id=template.exam_version_id, status=CandidateAssignmentStatus.ASSIGNED, maximum_attempts=1, created_at=datetime.now(timezone.utc)))
                session.commit()
                print(f"Demo data already present for {ORG_SLUG}; repaired the candidate walkthrough assignment.")
            else:
                print(f"Demo data already present for {ORG_SLUG}; no changes made.")
            return False

        # The fixture is intentionally imported from the canonical integration support
        # package so the demo exercises real production ORM relationships and migrations.
        api_root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(api_root))
        from tests.integration_support.result_correction_scenario import (  # noqa: PLC0415
            build_governed_result_correction_scenario,
        )

        seed_defaults(session)
        scenario = build_governed_result_correction_scenario(session)
        scenario.organisation.slug = ORG_SLUG
        scenario.organisation.name = "PrüfAI Demo Academy — Synthetic Walkthrough"
        scenario.standard_pack_version.standard_pack.name = (
            "PrüfAI Demo Speaking Pack — SYNTHETIC DEMO DATA"
        )
        scenario.exam.name = "Demo B1 Speaking Check — NOT OFFICIAL MATERIAL"
        session.add(
            CandidateExamAssignment(
                organisation_id=scenario.organisation.id,
                candidate_user_id=scenario.second_candidate.id,
                exam_version_id=scenario.exam_version.id,
                status=CandidateAssignmentStatus.ASSIGNED,
                maximum_attempts=1,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.flush()

        users = {
            "candidate@demo.prufai.local": scenario.second_candidate,
            "reviewer@demo.prufai.local": scenario.reviewer,
            "history@demo.prufai.local": scenario.candidate,
        }
        for email, user in users.items():
            _set_identity(user, email, {"candidate@demo.prufai.local": "Demo Candidate", "reviewer@demo.prufai.local": "Demo Reviewer", "history@demo.prufai.local": "Demo Candidate (submitted attempt)"}[email])
            AuthenticationService(settings).set_password(session, user, PASSWORD)

        admin = User(email="admin@demo.prufai.local", normalized_email="admin@demo.prufai.local", display_name="Demo Institution Admin", status=UserStatus.ACTIVE)
        session.add(admin)
        session.flush()
        AuthenticationService(settings).set_password(session, admin, PASSWORD)
        session.flush()

        role_by_key = {role.key: role for role in session.scalars(select(Role).where(Role.scope == RoleScope.TENANT))}
        memberships = list(session.scalars(select(OrganisationMembership).where(OrganisationMembership.organisation_id == scenario.organisation.id)))
        membership_by_user = {membership.user_id: membership for membership in memberships}
        session.add(OrganisationMembership(organisation_id=scenario.organisation.id, user_id=admin.id))
        session.flush()
        admin_membership = session.scalar(select(OrganisationMembership).where(OrganisationMembership.organisation_id == scenario.organisation.id, OrganisationMembership.user_id == admin.id))
        assignments = [(membership_by_user[scenario.second_candidate.id], "EXAMINER"), (membership_by_user[scenario.reviewer.id], "EXAM_MANAGER"), (admin_membership, "INSTITUTION_ADMIN")]
        for membership, role_key in assignments:
            session.add(MembershipRole(membership_id=membership.id, role_id=role_by_key[role_key].id))
        session.commit()
        print("Created synthetic PrüfAI demo data.")
        print(f"Organisation: {ORG_SLUG}")
        for email in (*ACCOUNTS, "history@demo.prufai.local"):
            print(f"{email} / {PASSWORD}")
        return True


if __name__ == "__main__":
    seed()
