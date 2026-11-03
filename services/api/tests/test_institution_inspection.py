import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization.seed import seed_defaults
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import MembershipRole, OrganisationMembership, Permission, Role, RolePermission
from app.models.enums import RoleScope
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)


@pytest.mark.parametrize("can_read_results", [True, False])
def test_institution_inspection_requires_real_permissions_and_scopes_results(
    session: Session, can_read_results: bool
) -> None:
    seed_defaults(session)
    scenario = build_governed_result_correction_scenario(session)
    membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == scenario.reviewer.id,
            OrganisationMembership.organisation_id == scenario.organisation.id,
        )
    )
    if can_read_results:
        role = session.scalar(select(Role).where(Role.key == "EXAM_MANAGER"))
    else:
        role = Role(key=f"INSPECTION_ONLY_{uuid.uuid4().hex}", scope=RoleScope.TENANT)
        session.add(role)
        session.flush()
        permission = session.scalar(
            select(Permission).where(Permission.key == "exam_assignment.read")
        )
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
    settings = Settings(environment="test", auth_jwt_secret="inspection-test-" + "x" * 40)
    for user in (scenario.reviewer, scenario.candidate):
        user.normalized_email = user.email.casefold()
        AuthenticationService(settings).set_password(session, user, "correct horse battery staple")
    session.commit()

    def override_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[auth_settings_dependency] = lambda: settings
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        path = f"/api/v1/organisations/{scenario.organisation.id}/exam-assignments"
        assert client.get(path).status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"email": scenario.reviewer.email, "password": "correct horse battery staple"},
        )
        assert login.status_code == 200
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-PrufAI-Organisation-ID": str(scenario.organisation.id),
        }
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        assignment = response.json()[0]
        assert assignment["candidate_user_id"] == str(scenario.candidate.id)
        assert assignment["candidate_name"] == scenario.candidate.display_name
        assert assignment["exam_name"] == scenario.exam.name
        assert assignment["exam_version_id"] == str(scenario.exam_version.id)
        assert assignment["attempts"][0]["status"] == "SUBMITTED"
        result = assignment["attempts"][0]["result"]
        if can_read_results:
            assert result["id"] == str(scenario.assessment_result.id)
            assert result["version_id"] == str(scenario.result_version_r1.id)
            assert result["status"] == "RELEASED"
            assert Decimal(result["raw_awarded_points"]) == Decimal("1")
            assert result["checksum"] == scenario.result_version_r1.checksum
        else:
            assert result is None
        assert (
            client.get(
                path,
                headers={**headers, "X-PrufAI-Organisation-ID": str(uuid.uuid4())},
            ).status_code
            == 401
        )
        candidate_login = client.post(
            "/api/v1/auth/login",
            json={"email": scenario.candidate.email, "password": "correct horse battery staple"},
        )
        assert candidate_login.status_code == 200
        assert (
            client.get(
                path,
                headers={
                    **headers,
                    "Authorization": f"Bearer {candidate_login.json()['access_token']}",
                },
            ).status_code
            == 403
        )
    finally:
        app.dependency_overrides.clear()
