"""Install the Berlin LiD synthetic fixture only in an explicit disposable test database."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext, build_tenant_context
from app.config import get_settings
from app.db import create_session_factory
from app.evaluation.disposable import assert_disposable_database
from app.exams.service import ExamStudioService, exam_checksum
from app.lid_policy import LID_EXAM_FAMILY, LID_PRACTICE_CODE_PREFIX, LID_PRACTICE_SCHEMA
from app.lid_practice import service as lid
from app.lid_practice.fixture_content import BERLIN_SCENARIOS, GENERAL_SCENARIOS, Scenario
from app.models import (
    Exam,
    ExamVersion,
    LidPracticeForm,
    LidPracticePoolVersion,
    QuestionBankItemVersion,
    StandardPack,
    StandardPackVersion,
    User,
)
from app.models.enums import (
    AssessmentFamily,
    ExamVersionStatus,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankTaskType,
    StandardPackStatus,
)
from app.question_bank.service import QuestionBankService
from app.scripts.seed_demo import demo_id, require_demo_environment
from app.standard_packs.lid_service import build_lid_structure, lid_draft_integrity
from app.standard_packs.service import StandardPackService

BERLIN = "DE-BE"
FIXTURE_LABEL = "original-synthetic-v1"
FIXTURE_BASIS = "TEST ONLY. Original practice scenario. No official content or exam approval."


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
    parser.add_argument("--blueprint-version-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    settings = get_settings()
    require_demo_environment(settings, enabled=os.environ.get("PRUFAI_DEMO_ENABLED") == "1")
    database = assert_disposable_database(os.environ.get("DATABASE_URL", ""))
    with create_session_factory(settings)() as session:
        blueprint = session.get(StandardPackVersion, args.blueprint_version_id)
        if blueprint is None:
            raise ValueError("LID_DRAFT_BLUEPRINT_REQUIRED")
        organisation_id = demo_id("organisation")
        administrator = session.scalar(
            select(User).where(User.normalized_email == "admin@demo.prufai.local")
        )
        if administrator is None:
            raise ValueError("LID_SYNTHETIC_FIXTURE_DEMO_INSTITUTION_REQUIRED")
        version = seed_lid_practice(
            session,
            disposable_database=database,
            blueprint=blueprint,
            author=build_tenant_context(session, administrator.id, organisation_id),
            reviewer=build_tenant_context(session, demo_id("platform-admin"), organisation_id),
        )
        session.commit()
        print(
            json.dumps(
                {
                    "exam_version_id": str(version.id),
                    "exam_name": version.exam.name,
                    "bundesland": BERLIN,
                }
            )
        )


if __name__ == "__main__":
    main()
