import os
import uuid
from dataclasses import replace

import psycopg
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.authorization import TenantContext
from app.models.enums import (
    AppealDecision,
    AppealStatus,
    CorrectionStatus,
    CorrectionType,
    ModerationStatus,
)
from app.models.result_governance import (
    AppealInformationEntry,
    ResultAppeal,
    ResultAppealVersion,
    ResultCorrection,
    ResultCorrectionObjectiveBinding,
    ResultModerationCase,
)
from app.result_governance.service import ResultGovernanceError, ResultGovernanceService
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for result-governance integration",
)


@pytest.fixture
def governance_session():
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    engine = create_engine(configured_url)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            yield session
            session.rollback()
    finally:
        engine.dispose()


def test_governance_orm_columns_match_migrated_postgresql(governance_session) -> None:
    """Reflect real Alembic tables, not ORM-created replacement tables."""
    database = inspect(governance_session.get_bind())
    for model in (
        ResultAppeal,
        ResultAppealVersion,
        AppealInformationEntry,
        ResultModerationCase,
        ResultCorrection,
        ResultCorrectionObjectiveBinding,
    ):
        mapped_columns = {column.name for column in inspect(model).columns}
        actual_columns = {
            column["name"] for column in database.get_columns(model.__tablename__, schema="public")
        }
        assert mapped_columns == actual_columns, model.__tablename__
        # Compile and execute every mapped column even when the table has no rows.
        governance_session.execute(select(model).limit(0)).all()

    for model in (ResultModerationCase, ResultCorrection):
        metadata_column = inspect(model).column_attrs.metadata_json.columns[0]
        assert metadata_column.name == "metadata"
        assert not metadata_column.nullable
        assert metadata_column.default.is_callable
        assert "metadata_json" not in {
            column["name"] for column in database.get_columns(model.__tablename__)
        }
        governance_session.execute(select(model.metadata_json).limit(0)).all()


def test_moderation_service_persists_metadata_and_preserves_history(governance_session) -> None:
    session = governance_session
    scenario = build_governed_result_correction_scenario(session)
    context = TenantContext(
        user_id=scenario.reviewer.id,
        organisation_id=scenario.organisation.id,
        membership_id=None,
        permissions=frozenset({"result.moderate"}),
    )
    original_result = scenario.result_version_r1.checksum
    original_run = scenario.scoring_run_s1.checksum
    create_operation = str(uuid.uuid4())
    decision_operation = str(uuid.uuid4())
    case = ResultGovernanceService.create_moderation_case(
        session,
        context,
        result_version_id=scenario.result_version_r1.id,
        reason="metadata schema contract",
        operation_id=create_operation,
    )
    session.expire(case)
    assert case.metadata_json == {}
    assert (
        ResultGovernanceService.create_moderation_case(
            session,
            context,
            result_version_id=scenario.result_version_r1.id,
            reason="metadata schema contract",
            operation_id=create_operation,
        ).id
        == case.id
    )

    with pytest.raises(ResultGovernanceError, match="moderation case not found"):
        ResultGovernanceService.decide_moderation(
            session,
            replace(context, organisation_id=uuid.uuid4()),
            case_id=case.id,
            result_checksum=case.result_checksum,
            outcome=ModerationStatus.NO_CHANGE,
            operation_id=str(uuid.uuid4()),
        )
    with pytest.raises(ResultGovernanceError, match="stale moderation case"):
        ResultGovernanceService.decide_moderation(
            session,
            context,
            case_id=case.id,
            result_checksum="stale",
            outcome=ModerationStatus.NO_CHANGE,
            operation_id=str(uuid.uuid4()),
        )
    ResultGovernanceService.decide_moderation(
        session,
        context,
        case_id=case.id,
        result_checksum=case.result_checksum,
        outcome=ModerationStatus.NO_CHANGE,
        operation_id=decision_operation,
    )
    session.flush()
    session.expire(case)
    assert case.status is ModerationStatus.NO_CHANGE
    assert case.metadata_json == {"decision_operation_id": decision_operation}
    assert (
        session.scalar(
            select(ResultModerationCase.metadata_json).where(ResultModerationCase.id == case.id)
        )
        == case.metadata_json
    )
    assert (
        ResultGovernanceService.decide_moderation(
            session,
            context,
            case_id=case.id,
            result_checksum=case.result_checksum,
            outcome=ModerationStatus.NO_CHANGE,
            operation_id=decision_operation,
        ).id
        == case.id
    )
    session.expire_all()
    assert scenario.result_version_r1.checksum == original_result
    assert scenario.scoring_run_s1.checksum == original_run


def test_failed_governed_correction_keeps_metadata_and_source_lineage(governance_session) -> None:
    """A missing governed replacement is rejected without altering released history."""
    session = governance_session
    scenario = build_governed_result_correction_scenario(session)
    original_result_checksum = scenario.result_version_r1.checksum
    original_run_checksum = scenario.scoring_run_s1.checksum
    context = TenantContext(
        user_id=scenario.reviewer.id,
        organisation_id=scenario.organisation.id,
        membership_id=None,
        permissions=frozenset({"appeal.adjudicate", "appeal.correct", "audit.read"}),
    )
    # Use the real submission service to pin the current evidence bundle, rather
    # than editing the fixture's historical appeal version to manufacture approval.
    candidate_context = replace(context, user_id=scenario.candidate.id)
    ResultGovernanceService.transition(
        session, candidate_context, scenario.appeal.id, AppealStatus.WITHDRAWN
    )
    appeal = ResultGovernanceService.submit_appeal(
        session,
        candidate_context,
        result_version_id=scenario.result_version_r1.id,
        category="CORRECTION",
        grounds="synthetic metadata regression",
        operation_id=str(uuid.uuid4()),
    )
    ResultGovernanceService.transition(session, context, appeal.id, AppealStatus.UNDER_REVIEW)
    session.flush()
    current = session.scalar(
        select(ResultAppealVersion).where(ResultAppealVersion.appeal_id == appeal.id)
    )
    ResultGovernanceService.adjudicate(
        session,
        context,
        appeal_id=appeal.id,
        appeal_version_number=current.version_number,
        appeal_checksum=current.checksum,
        evidence_bundle_checksum=current.evidence_bundle_checksum,
        decision=AppealDecision.UPHOLD,
        operation_id=str(uuid.uuid4()),
        reason="human-approved investigation",
    )
    session.flush()
    decision = session.scalar(
        select(ResultAppealVersion).where(
            ResultAppealVersion.appeal_id == appeal.id,
            ResultAppealVersion.version_number == appeal.current_version_number,
        )
    )
    operation_id = str(uuid.uuid4())
    correction_args = dict(
        appeal_id=appeal.id,
        appeal_version_number=decision.version_number,
        appeal_checksum=decision.checksum,
        evidence_bundle_checksum=decision.evidence_bundle_checksum,
        correction_type=CorrectionType.OBJECTIVE_RESCORING,
        operation_id=operation_id,
    )
    with pytest.raises(ResultGovernanceError, match="appeal not found"):
        ResultGovernanceService.initiate_correction(
            session, replace(context, organisation_id=uuid.uuid4()), **correction_args
        )
    with pytest.raises(ResultGovernanceError, match="governed replacements"):
        ResultGovernanceService.initiate_correction(session, context, **correction_args)
    session.flush()
    session.expire_all()
    correction = session.scalar(
        select(ResultCorrection).where(ResultCorrection.operation_id == operation_id)
    )
    assert correction.status is CorrectionStatus.FAILED
    assert correction.failure_category == "ResultGovernanceError"
    assert correction.metadata_json == {
        "evidence_bundle_checksum": decision.evidence_bundle_checksum
    }
    assert correction.appeal_id == appeal.id
    assert correction.appeal_version_id == decision.id
    assert correction.source_result_version_id == scenario.result_version_r1.id
    assert correction.source_result_checksum == scenario.result_version_r1.checksum
    assert correction.new_result_version_id is None
    assert correction.new_scoring_run_id is None
    assert (
        ResultGovernanceService.initiate_correction(session, context, **correction_args).id
        == correction.id
    )
    with pytest.raises(ResultGovernanceError, match="appeal not found"):
        ResultGovernanceService.initiate_correction(
            session, replace(context, organisation_id=uuid.uuid4()), **correction_args
        )
    assert scenario.result_version_r1.status.value == "RELEASED"
    assert scenario.result_version_r1.checksum == original_result_checksum
    assert scenario.scoring_run_s1.checksum == original_run_checksum
    assert len(scenario.assessment_result.versions) == 1


def test_result_governance_schema_is_migration_backed() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    url = configured_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {
            "result_appeals",
            "result_appeal_versions",
            "appeal_information_entries",
            "result_moderation_cases",
            "result_corrections",
            "assessment_results",
            "result_versions",
            "scoring_runs",
        } <= tables
        constraints = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name, constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name IN (
                    'result_appeals',
                    'result_appeal_versions',
                    'result_moderation_cases',
                    'result_corrections'
                )
                """
            )
        }
        assert {
            ("result_appeals", "uq_result_appeal_operation"),
            ("result_appeal_versions", "uq_result_appeal_version_number"),
            ("result_appeal_versions", "uq_result_appeal_version_operation"),
            ("result_moderation_cases", "uq_result_moderation_operation"),
            ("result_corrections", "uq_result_correction_operation"),
        } <= constraints
        columns = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name IN
                ('result_appeals', 'result_appeal_versions')
                """
            )
        }
        assert ("result_appeals", "challenged_result_version_id") in columns
        assert ("result_appeal_versions", "evidence_bundle_checksum") in columns
        correction_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'result_corrections'
                """
            )
        }
        assert {
            "appeal_version_id",
            "source_result_version_id",
            "correction_type",
            "status",
            "new_scoring_run_id",
            "new_result_version_id",
        } <= correction_columns
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
