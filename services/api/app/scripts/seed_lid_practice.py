"""Install the Berlin LiD synthetic fixture only in an explicit disposable test database."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext, build_tenant_context
from app.config import Settings, get_settings
from app.db import create_session_factory
from app.evaluation.disposable import assert_disposable_database
from app.exams.service import ExamStudioService, exam_checksum
from app.lid_policy import LID_EXAM_FAMILY, LID_PRACTICE_CODE_PREFIX, LID_PRACTICE_SCHEMA
from app.lid_practice import service as lid
from app.lid_practice.fixture_content import BERLIN_SCENARIOS, GENERAL_SCENARIOS, Scenario
from app.models import (
    CandidateExamAssignment,
    Exam,
    ExamVersion,
    LidPracticeForm,
    LidPracticePoolVersion,
    Organisation,
    PlatformRoleAssignment,
    QuestionBankItemVersion,
    Role,
    StandardPack,
    StandardPackVersion,
    User,
)
from app.models.enums import (
    AssessmentFamily,
    CandidateAssignmentStatus,
    ExamVersionStatus,
    PackResponseMode,
    PlatformAssignmentStatus,
    QuestionBankLifecycleStatus,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankTaskType,
    RoleScope,
    StandardPackStatus,
    UserStatus,
)
from app.question_bank.service import QuestionBankService
from app.runtime.service import CandidateRuntimeService
from app.scripts.seed_demo import demo_id, reconcile_demo, require_demo_environment
from app.services.knowledge import KnowledgeRegistryService
from app.standard_packs.lid_service import (
    build_lid_structure,
    create_lid_blueprint,
    lid_draft_integrity,
)
from app.standard_packs.lid_sources import LID_ENTRIES, register_lid_source
from app.standard_packs.service import StandardPackService

BERLIN = "DE-BE"
FIXTURE_LABEL = "original-synthetic-v1"
FIXTURE_BASIS = "TEST ONLY. Original practice scenario. No official content or exam approval."
SOURCE_REVIEWER_EMAIL = "lid-source-reviewer@prufai.invalid"
SOURCE_REVIEWER_NAME = "TEST ONLY LiD metadata reviewer"


def _demo_source_reviewer(session: Session) -> TenantContext:
    reviewer_id = demo_id("lid-fixture-source-reviewer")
    role = session.scalar(
        select(Role).where(Role.key == "PRUFAI_STANDARDS_ADMIN", Role.scope == RoleScope.PLATFORM)
    )
    if role is None:
        raise ValueError("LID_SYNTHETIC_FIXTURE_DEMO_INSTITUTION_REQUIRED")
    reviewer = session.scalar(select(User).where(User.normalized_email == SOURCE_REVIEWER_EMAIL))
    if reviewer is None:
        if session.get(User, reviewer_id) is not None:
            raise ValueError("LID_SYNTHETIC_FIXTURE_REVIEWER_CONFLICT")
        reviewer = User(
            id=reviewer_id,
            email=SOURCE_REVIEWER_EMAIL,
            normalized_email=SOURCE_REVIEWER_EMAIL,
            display_name=SOURCE_REVIEWER_NAME,
        )
        session.add(reviewer)
        session.add(PlatformRoleAssignment(user=reviewer, role=role))
        session.flush()
    if (
        reviewer.id != reviewer_id
        or reviewer.display_name != SOURCE_REVIEWER_NAME
        or reviewer.status is not UserStatus.ACTIVE
        or reviewer.password_credential is not None
        or reviewer.memberships
        or [(assignment.role_id, assignment.status) for assignment in reviewer.platform_assignments]
        != [(role.id, PlatformAssignmentStatus.ACTIVE)]
    ):
        raise ValueError("LID_SYNTHETIC_FIXTURE_REVIEWER_CONFLICT")
    return build_tenant_context(session, reviewer.id)


def _prepare_demo_blueprint(session: Session, platform_user_id: uuid.UUID) -> StandardPackVersion:
    author = build_tenant_context(session, platform_user_id)
    reviewer = _demo_source_reviewer(session)
    registry = KnowledgeRegistryService()
    pins: dict[str, tuple[uuid.UUID, str]] = {}
    for key, entry in LID_ENTRIES.items():
        version = register_lid_source(session, author, key)
        checksum = version.metadata_checksum
        if checksum is None or version.created_by_user_id != author.user_id:
            raise ValueError("LID_SYNTHETIC_FIXTURE_SOURCE_CONFLICT")
        registry.review_rights(
            session,
            reviewer,
            version_id=version.id,
            expected_metadata_checksum=checksum,
            decision="METADATA_ONLY",
            basis_reference="TEST ONLY. Simulated review for disposable LiD practice.",
            rights_holder=entry.rights_holder,
            licence_type="TEST ONLY metadata reference",
            retention_policy="Disposable LiD demo fixture",
            commit=False,
        )
        pins[key] = (version.id, checksum)
    return create_lid_blueprint(session, author, pins)


def _prepare_demo_database(session: Session, settings: Settings) -> None:
    migrations = Path(__file__).resolve().parents[2] / "alembic"
    heads = ScriptDirectory(str(migrations)).get_heads()
    if (
        len(heads) != 1
        or session.scalar(text("SELECT to_regclass('public.alembic_version')")) is None
        or list(session.scalars(text("SELECT version_num FROM public.alembic_version"))) != heads
    ):
        raise ValueError("LID_SYNTHETIC_FIXTURE_ALEMBIC_HEAD_REQUIRED")
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('prufai-lid-demo-preparation'))"))
    if session.get(Organisation, demo_id("organisation")) is not None:
        return
    if session.scalar(select(User.id).limit(1)) or session.scalar(select(Organisation.id).limit(1)):
        raise ValueError("LID_SYNTHETIC_FIXTURE_EMPTY_DATABASE_REQUIRED")
    password = os.environ.get("PRUFAI_DEMO_PASSWORD")
    if not password:
        raise ValueError("LID_SYNTHETIC_FIXTURE_EXPLICIT_DEMO_PASSWORD_REQUIRED")
    reconcile_demo(session, settings, enabled=True, password=password, commit=False)


def _prepare_demo_assignment(
    session: Session, author: TenantContext, version: ExamVersion
) -> CandidateExamAssignment:
    candidate = session.scalar(
        select(User).where(User.normalized_email == "candidate@demo.prufai.local")
    )
    if candidate is None:
        raise ValueError("LID_SYNTHETIC_FIXTURE_DEMO_CANDIDATE_REQUIRED")
    context = build_tenant_context(session, candidate.id, author.organisation_id)
    if context.membership_id is None:
        raise ValueError("LID_SYNTHETIC_FIXTURE_DEMO_CANDIDATE_REQUIRED")
    existing = list(
        session.scalars(
            select(CandidateExamAssignment).where(
                CandidateExamAssignment.organisation_id == author.organisation_id,
                CandidateExamAssignment.exam_version_id == version.id,
                CandidateExamAssignment.candidate_user_id == candidate.id,
            )
        )
    )
    if not existing:
        return CandidateRuntimeService.create_assignment(
            session, author, candidate_user_id=candidate.id, exam_version_id=version.id
        )
    assignment = existing[0]
    if (
        len(existing) != 1
        or assignment.created_by_user_id != author.user_id
        or assignment.status
        not in {
            CandidateAssignmentStatus.ASSIGNED,
            CandidateAssignmentStatus.COMPLETED,
            CandidateAssignmentStatus.AVAILABLE,
        }
        or assignment.maximum_attempts != 1
        or assignment.available_from is not None
        or assignment.available_until is not None
    ):
        raise ValueError("LID_SYNTHETIC_FIXTURE_ASSIGNMENT_CONFLICT")
    return assignment


def item_content(index: int, scenario: Scenario) -> dict[str, Any]:
    correct_slot = (index - 1) % 4
    choices = list(scenario.distractors)
    choices.insert(correct_slot, scenario.answer)
    return {
        "kind": "MULTIPLE_CHOICE",
        "prompt": f"{lid.DISCLOSURE}\n\n{scenario.prompt}",
        "options": [
            {"option_id": f"option-{slot + 1}", "display_text": value, "order": slot}
            for slot, value in enumerate(choices)
        ],
        "correct_option_ids": [f"option-{correct_slot + 1}"],
    }


def _make_item(
    session: Session,
    author: TenantContext,
    reviewer: TenantContext,
    pack: StandardPackVersion,
    task_id: uuid.UUID,
    index: int,
    scenario: Scenario,
) -> QuestionBankItemVersion:
    item = QuestionBankService.create_item(
        session,
        author,
        code=f"LID_SYNTHETIC_{index:02d}",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=author.organisation_id,
    )
    version = QuestionBankService.create_version(
        session,
        author,
        item,
        version_label=f"original-synthetic-{index:02d}-v1",
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family=LID_EXAM_FAMILY,
        language="de",
        task_type=QuestionBankTaskType.MULTIPLE_CHOICE,
        response_mode=PackResponseMode.SELECT_ONE,
        content=item_content(index, scenario),
        scoring_policy=QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        max_points=Decimal("1"),
        scoring_metadata=lid.OBJECTIVE_POLICY,
        rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
        rights_holder="PrüfAI synthetic fixture authors",
        licence_reference="PRUFAI_ORIGINAL_TEST_FIXTURE",
    )
    QuestionBankService.bind_pack(session, author, version, pack.id, task_id)
    review = QuestionBankService.request_review(session, reviewer, version)
    validation = QuestionBankService.validate_pack_compatibility(session, version, pack.id, task_id)
    QuestionBankService.approve_review(session, reviewer, review, validation)
    for target in (QuestionBankLifecycleStatus.PUBLISHED, QuestionBankLifecycleStatus.ACTIVE):
        QuestionBankService.transition(
            session, reviewer, version, target, validation, version.checksum
        )
    return version


def _practice_pack(
    session: Session, code: str, author: TenantContext, now: datetime
) -> StandardPackVersion:
    pack = StandardPack(
        code=code,
        name=f"{lid.TITLE} — TEST ONLY",
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family=LID_EXAM_FAMILY,
        language="de",
        status=StandardPackStatus.ACTIVE,
        description=FIXTURE_BASIS,
    )
    version = StandardPackVersion(
        standard_pack=pack,
        version_label=FIXTURE_LABEL,
        status=StandardPackStatus.ACTIVE,
        created_at=now,
        created_by_user_id=author.user_id,
        notes=FIXTURE_BASIS,
        configuration_schema_version=LID_PRACTICE_SCHEMA,
        supported_levels=[],
        allowed_classifications=["PRACTICE"],
    )
    build_lid_structure(version, now)
    session.add(version)
    session.flush()
    version.configuration_checksum = StandardPackService.checksum(version)
    session.flush()
    return version


def _exact_replay(
    session: Session, pack: StandardPack, blueprint: StandardPackVersion, organisation_id: uuid.UUID
) -> ExamVersion:
    version = session.scalar(
        select(ExamVersion)
        .join(Exam)
        .where(Exam.code == pack.code, Exam.organisation_id == organisation_id)
    )
    if (
        version is None
        or len(pack.versions) != 1
        or version.standard_pack_version_id != pack.versions[0].id
    ):
        raise ValueError("LID_SYNTHETIC_FIXTURE_REPLAY_CONFLICT")
    form = lid.require_form(session, version, pack.versions[0])
    expected = {
        exam_checksum(item_content(index, scenario)): (scenario.topic, scenario.reference)
        for index, scenario in enumerate((*GENERAL_SCENARIOS, *BERLIN_SCENARIOS), 1)
    }
    pinned = {
        entry["content_checksum"]: (entry["topic"], entry["fact_reference"])
        for entry in form.manifest["items"]
    }
    if (
        form.blueprint_version_id != blueprint.id
        or form.bundesland != BERLIN
        or form.selection_seed != uuid.uuid5(blueprint.id, organisation_id.hex).hex
        or pinned != expected
        or version.status is not ExamVersionStatus.ACTIVE
    ):
        raise ValueError("LID_SYNTHETIC_FIXTURE_REPLAY_CONFLICT")
    return version


def seed_lid_practice(
    session: Session,
    *,
    disposable_database: str,
    blueprint: StandardPackVersion,
    author: TenantContext,
    reviewer: TenantContext,
) -> ExamVersion:
    bind = session.get_bind()
    assert_disposable_database(
        bind.engine.url.render_as_string(hide_password=False), disposable_database
    )
    if (
        get_settings().environment == "production"
        or author.organisation_id is None
        or author.membership_id is None
        or not reviewer.platform_scope
        or reviewer.organisation_id != author.organisation_id
        or reviewer.user_id == author.user_id
        or lid_draft_integrity(session, blueprint) is not None
    ):
        raise ValueError("LID_SYNTHETIC_FIXTURE_PREREQUISITES")
    organisation_id = author.organisation_id
    code = LID_PRACTICE_CODE_PREFIX + organisation_id.hex.upper()
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:code))"), {"code": code})
    existing = session.scalar(select(StandardPack).where(StandardPack.code == code))
    if existing is not None:
        return _exact_replay(session, existing, blueprint, organisation_id)

    now = datetime.now(timezone.utc)
    pack = _practice_pack(session, code, author, now)
    exam = ExamStudioService.create_exam(
        session,
        author,
        code=code,
        name=lid.TITLE,
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=organisation_id,
        description=FIXTURE_BASIS,
    )
    version = ExamStudioService.create_version(
        session,
        author,
        exam,
        version_label=FIXTURE_LABEL,
        standard_pack_version_id=pack.id,
    )
    ExamStudioService.materialize_blueprint(session, author, version)
    selection_seed = uuid.uuid5(blueprint.id, organisation_id.hex).hex
    pools = []
    offset = 0
    for section, scope, jurisdiction, scenarios in zip(
        version.modules[0].sections,
        (lid.GENERAL, lid.STATE),
        ("DE", BERLIN),
        (GENERAL_SCENARIOS, BERLIN_SCENARIOS),
        strict=True,
    ):
        task = section.tasks[0]
        pins = []
        for index, scenario in enumerate(scenarios, offset + 1):
            item = _make_item(
                session, author, reviewer, pack, task.standard_pack_task_spec_id, index, scenario
            )
            pins.append(
                lid.item_pin(
                    item, organisation_id, topic=scenario.topic, fact_reference=scenario.reference
                )
            )
        offset += len(scenarios)
        pool = LidPracticePoolVersion(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            practice_pack_version_id=pack.id,
            scope=scope,
            jurisdiction=jurisdiction,
            version_label=FIXTURE_LABEL,
            created_at=now,
        )
        pool.manifest = lid.pool_manifest(pool, blueprint, pins)
        pool.checksum = exam_checksum(pool.manifest)
        session.add(pool)
        session.flush()
        pools.append(pool)
        count = lid.GENERAL_COUNT if scope == lid.GENERAL else lid.STATE_COUNT
        for slot, entry in enumerate(lid.select_items(pool, selection_seed, count)):
            ExamStudioService.assign_item(
                session,
                author,
                version,
                task_id=task.id,
                item_version_id=uuid.UUID(entry["item_version_id"]),
                slot_index=slot,
            )

    general, state = pools
    manifest = lid.form_manifest(session, version, pack, blueprint, general, state, selection_seed)
    form = LidPracticeForm(
        exam_version_id=version.id,
        blueprint_version_id=blueprint.id,
        general_pool_version_id=general.id,
        state_pool_version_id=state.id,
        bundesland=BERLIN,
        selection_version=lid.SELECTION_VERSION,
        selection_seed=selection_seed,
        manifest=manifest,
        checksum=exam_checksum(manifest),
        created_at=now,
    )
    session.add(form)
    session.flush()
    ExamStudioService.request_review(session, reviewer, version)
    for target in (
        ExamVersionStatus.APPROVED,
        ExamVersionStatus.PUBLISHED,
        ExamVersionStatus.ACTIVE,
    ):
        ExamStudioService.transition(session, reviewer, version, target)
    AuditService.record(
        session,
        author,
        event_type="lid.practice.form_generated",
        entity_type="EXAM_VERSION",
        organisation_id=organisation_id,
        entity_id=str(version.id),
        require_audit_permission=False,
        metadata={
            "bundesland": form.bundesland,
            "form_checksum": form.checksum,
            "general_count": lid.GENERAL_COUNT,
            "state_count": lid.STATE_COUNT,
        },
    )
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--blueprint-version-id", type=uuid.UUID)
    mode.add_argument(
        "--prepare-demo",
        action="store_true",
        help="Prepare the synthetic blueprint, exam and candidate assignment in a disposable demo.",
    )
    args = parser.parse_args()
    settings = get_settings()
    require_demo_environment(settings, enabled=os.environ.get("PRUFAI_DEMO_ENABLED") == "1")
    database = assert_disposable_database(os.environ.get("DATABASE_URL", ""))
    assert_disposable_database(settings.database_url, database, report=False)
    if args.prepare_demo and os.environ.get("ENVIRONMENT") != "development":
        raise ValueError("LID_SYNTHETIC_FIXTURE_EXPLICIT_DEVELOPMENT_REQUIRED")
    with create_session_factory(settings)() as session:
        if session.execute(text("SELECT current_database(), current_user")).one() != (
            database,
            "prufai",
        ):
            raise ValueError("DISPOSABLE_DATABASE_REQUIRED")
        if args.prepare_demo:
            _prepare_demo_database(session, settings)
        organisation_id = demo_id("organisation")
        platform_user_id = demo_id("platform-admin")
        administrator = session.scalar(
            select(User).where(User.normalized_email == "admin@demo.prufai.local")
        )
        if administrator is None:
            raise ValueError("LID_SYNTHETIC_FIXTURE_DEMO_INSTITUTION_REQUIRED")
        blueprint = (
            _prepare_demo_blueprint(session, platform_user_id)
            if args.prepare_demo
            else session.get(StandardPackVersion, args.blueprint_version_id)
        )
        if blueprint is None:
            raise ValueError("LID_DRAFT_BLUEPRINT_REQUIRED")
        author = build_tenant_context(session, administrator.id, organisation_id)
        version = seed_lid_practice(
            session,
            disposable_database=database,
            blueprint=blueprint,
            author=author,
            reviewer=build_tenant_context(session, platform_user_id, organisation_id),
        )
        assignment = (
            _prepare_demo_assignment(session, author, version) if args.prepare_demo else None
        )
        session.commit()
        print(
            json.dumps(
                {
                    "exam_version_id": str(version.id),
                    "exam_name": version.exam.name,
                    "bundesland": BERLIN,
                    "blueprint_version_id": str(blueprint.id),
                    **({"candidate_assignment_id": str(assignment.id)} if assignment else {}),
                }
            )
        )


if __name__ == "__main__":
    main()
