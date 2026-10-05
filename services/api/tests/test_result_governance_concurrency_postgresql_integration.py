import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.authorization import TenantContext
from app.models import ResultAppeal, ResultAppealVersion, ResultCorrection
from app.models.enums import AppealDecision, AppealStatus
from app.result_governance.service import ResultGovernanceError, ResultGovernanceService
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for concurrency integration",
)


def test_postgres_concurrent_adjudication_allows_only_one_authoritative_transition() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    engine = create_engine(configured_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    bootstrap = session_factory()
    try:
        scenario = build_governed_result_correction_scenario(bootstrap)
        evidence_bundle = ResultGovernanceService.build_evidence_bundle(
            bootstrap, scenario.result_version_r1.id
        )
        scenario.appeal.status = AppealStatus.UNDER_REVIEW
        scenario.appeal.current_checksum = scenario.appeal_version.checksum
        scenario.appeal_version.status = AppealStatus.UNDER_REVIEW
        scenario.appeal_version.evidence_bundle_checksum = evidence_bundle.checksum
        bootstrap.commit()

        appeal_id = scenario.appeal.id
        expected_version = scenario.appeal_version.version_number
        expected_checksum = scenario.appeal_version.checksum
        expected_evidence_checksum = evidence_bundle.checksum
        context = TenantContext(
            user_id=scenario.reviewer.id,
            organisation_id=scenario.organisation.id,
            membership_id=None,
            permissions=frozenset({"appeal.adjudicate"}),
        )
    finally:
        bootstrap.close()

    session_1: Session = session_factory()
    session_2: Session = session_factory()
    try:
        adjudicated = ResultGovernanceService.adjudicate(
            session_1,
            context,
            appeal_id=appeal_id,
            appeal_version_number=expected_version,
            appeal_checksum=expected_checksum,
            evidence_bundle_checksum=expected_evidence_checksum,
            decision=AppealDecision.UPHOLD,
            operation_id="fixture-adjudication-reviewer-1",
            reason="evidence upheld",
        )
        session_1.commit()
        assert adjudicated.status is AppealStatus.ADJUDICATED_UPHELD

        with pytest.raises(ResultGovernanceError, match="stale appeal version"):
            ResultGovernanceService.adjudicate(
                session_2,
                context,
                appeal_id=appeal_id,
                appeal_version_number=expected_version,
                appeal_checksum=expected_checksum,
                evidence_bundle_checksum=expected_evidence_checksum,
                decision=AppealDecision.REJECT,
                operation_id="fixture-adjudication-reviewer-2",
                reason="conflicting stale decision",
            )
        session_2.rollback()

        current = session_1.scalar(select(ResultAppeal).where(ResultAppeal.id == appeal_id))
        assert current is not None
        assert current.status is AppealStatus.ADJUDICATED_UPHELD
        assert current.current_version_number == expected_version + 1
        assert current.current_checksum != expected_checksum
        versions = list(
            session_1.scalars(
                select(ResultAppealVersion)
                .where(ResultAppealVersion.appeal_id == appeal_id)
                .order_by(ResultAppealVersion.version_number)
            )
        )
        assert len(versions) == 2
        assert versions[0].checksum == expected_checksum
        assert versions[1].decision is AppealDecision.UPHOLD
        assert versions[1].checksum == current.current_checksum
        assert (
            session_1.scalar(
                select(ResultCorrection).where(ResultCorrection.appeal_id == appeal_id)
            )
            is None
        )
    finally:
        session_1.close()
        session_2.close()
        engine.dispose()
