"""Seal and verify practice forms before delivery or canonical objective scoring."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.lid_policy import (
    LID_EDITORIAL_PACK_CODE_PREFIX,
    LID_EXAM_FAMILY,
    LID_ORIGINAL_NOTICE,
    LID_PRACTICE_CODE_PREFIX,
    LID_STATES,
    LID_STRUCTURE,
    is_lid_draft_pack,
    is_lid_editorial_pack,
    is_lid_practice_pack,
)
from app.models import (
    CandidateExamAttempt,
    ExamVersion,
    LidPracticeForm,
    LidPracticePoolVersion,
    QuestionBankItemCefrLevel,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
    StandardPackTaskSpecification,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankTaskType,
    StandardPackStatus,
)
from app.models.lid_editorial import (
    LidEditorialCollection,
    LidEditorialItemVersion,
    LidEditorialPublication,
)
from app.question_bank.schemas import MultipleChoiceContent

TITLE = "Leben in Deutschland — Practice"
DISCLOSURE = "ORIGINAL SYNTHETIC TEST CONTENT — NOT AN OFFICIAL BAMF QUESTION"
GENERAL = "GENERAL_DE"
STATE = "BUNDESLAND_SPECIFIC"
GENERAL_COUNT = LID_STRUCTURE["general_items"]
STATE_COUNT = LID_STRUCTURE["state_items"]
DURATION_SECONDS = LID_STRUCTURE["time_limit_seconds"]
SELECTION_VERSION = "s3-lid-sha256-item-order-v1"
OBJECTIVE_POLICY = {"unanswered_policy": {"type": "ZERO", "points": 0}}
INVALID_FORM = "LID_PRACTICE_FORM_PROVENANCE_INVALID"
CONTENT_UNAVAILABLE = "CONTENT_UNAVAILABLE"
ORIGINAL_MODE: Final = "PUBLISHED_PRACTICE"


def is_original_form(form: LidPracticeForm) -> bool:
    return form.manifest.get("mode") == ORIGINAL_MODE


def _checksum(payload: dict[str, Any]) -> str:
    from app.exams.service import exam_checksum

    return exam_checksum(payload)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def technical_delivery_enabled() -> bool:
    return get_settings().environment in {"development", "test"}


def require_attempt_environment(attempt: CandidateExamAttempt) -> None:
    practice = attempt.runtime_plan.get("lid_practice")
    if (
        isinstance(practice, dict)
        and practice.get("mode") != ORIGINAL_MODE
        and not technical_delivery_enabled()
    ):
        raise PermissionError("LID_SYNTHETIC_DELIVERY_DENIED")


def require_technical_pack(version: StandardPackVersion, exam: ExamVersion) -> uuid.UUID:
    organisation_id = exam.exam.organisation_id
    original = is_lid_editorial_pack(version)
    prefix = LID_EDITORIAL_PACK_CODE_PREFIX if original else LID_PRACTICE_CODE_PREFIX
    if (
        (not original and (not technical_delivery_enabled() or not is_lid_practice_pack(version)))
        or organisation_id is None
        or exam.exam.scope_type != "ORGANISATION_PRIVATE"
        or version.standard_pack.code != prefix + organisation_id.hex.upper()
        or version.standard_pack.assessment_family is not AssessmentFamily.INTEGRATION_KNOWLEDGE
        or version.standard_pack.language != "de"
        or version.standard_pack.status is not StandardPackStatus.ACTIVE
        or version.status is not StandardPackStatus.ACTIVE
        or version.supported_levels
        or version.allowed_classifications != [AssessmentClassification.PRACTICE.value]
        or exam.classification is not AssessmentClassification.PRACTICE
        or version.rule_bindings
        or version.source_bindings
        or exam.timing_override
        or exam.navigation_override
    ):
        raise ValueError(INVALID_FORM)
    return organisation_id


def item_pin(
    item: QuestionBankItemVersion,
    organisation_id: uuid.UUID,
    *,
    topic: str,
    fact_reference: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    content = MultipleChoiceContent.model_validate(item.content)
    if (
        item.item.organisation_id != organisation_id
        or item.item.scope_type != "ORGANISATION_PRIVATE"
        or item.status is not QuestionBankLifecycleStatus.ACTIVE
        or item.approved_at is None
        or item.approved_by_user_id is None
        or item.approved_by_user_id == item.created_by_user_id
        or item.published_at is None
        or item.activated_at is None
        or item.origin_type is not QuestionBankOriginType.HUMAN_AUTHORED
        or item.rights_status is not QuestionBankRightsStatus.PRUFAI_ORIGINAL
        or (item.rights_expires_at is not None and _utc(item.rights_expires_at) <= now)
        or (item.effective_from is not None and _utc(item.effective_from) > now)
        or (item.effective_until is not None and _utc(item.effective_until) <= now)
        or item.assessment_family is not AssessmentFamily.INTEGRATION_KNOWLEDGE
        or item.exam_family != LID_EXAM_FAMILY
        or item.language != "de"
        or item.skill is not None
        or item.task_type is not QuestionBankTaskType.MULTIPLE_CHOICE
        or item.response_mode is not PackResponseMode.SELECT_ONE
        or item.media_requirement is not PackMediaRequirement.NONE
        or item.scoring_policy is not QuestionBankScoringPolicy.OBJECTIVE_KEYED
        or item.max_points != 1
        or item.scoring_metadata != OBJECTIVE_POLICY
        or not content.prompt.startswith(DISCLOSURE + "\n\n")
        or len(content.options) != 4
        or {option.order for option in content.options} != set(range(4))
        or len(content.correct_option_ids) != 1
        or content.correct_option_ids[0] not in {option.option_id for option in content.options}
        or not topic
        or not fact_reference
    ):
        raise ValueError(INVALID_FORM)
    return {
        "item_id": str(item.item_id),
        "item_version_id": str(item.id),
        "item_checksum": item.checksum,
        "content_checksum": _checksum(item.content),
        "key_checksum": _checksum(
            {
                "item_version_id": str(item.id),
                "correct_option_ids": content.correct_option_ids,
                "maximum_points": 1,
                "scoring_metadata": item.scoring_metadata,
            }
        ),
        "topic": topic,
        "fact_reference": fact_reference,
    }


def original_item_pin(
    session: Session,
    item: QuestionBankItemVersion,
    organisation_id: uuid.UUID,
    *,
    historical: bool = False,
) -> dict[str, Any]:
    from app.lid_editorial.collections import validate_question
    from app.lid_editorial.reviews import answer_key_checksum, current_item_evidence
    from app.writing_assessment.service import canonical_checksum

    extension = session.get(LidEditorialItemVersion, item.id)
    if (
        extension is None
        or item.item.organisation_id != organisation_id
        or item.item.scope_type != "ORGANISATION_PRIVATE"
        or item.assessment_family is not AssessmentFamily.INTEGRATION_KNOWLEDGE
        or item.exam_family != LID_EXAM_FAMILY
        or item.language != "de"
        or item.max_points != 1
        or item.scoring_metadata != OBJECTIVE_POLICY
        or item.response_mode is not PackResponseMode.SELECT_ONE
        or not historical
        and item.status is not QuestionBankLifecycleStatus.ACTIVE
        or extension.provenance["item_checksum"] != item.checksum
        or extension.provenance["key_checksum"] != answer_key_checksum(item.content)
        or extension.checksum != canonical_checksum(extension.provenance)
    ):
        raise ValueError(INVALID_FORM)
    validate_question(item.content)
    if not historical:
        proof = current_item_evidence(session, item, mode=ORIGINAL_MODE, require_published=True)
        if not proof["eligible"] or not proof["genuine_approved"]:
            raise ValueError("PRACTICE_FORM_NOT_READY")
    return {
        "item_id": str(item.item_id),
        "item_version_id": str(item.id),
        "item_checksum": item.checksum,
        "content_checksum": _checksum(item.content),
        "key_checksum": extension.provenance["key_checksum"],
        "editorial_provenance_checksum": extension.checksum,
        "scope": extension.scope,
        "jurisdiction": extension.jurisdiction,
        "topic": extension.provenance["topic"],
        "fact_reference": extension.provenance["source"]["version_id"],
    }


def pool_manifest(
    pool: LidPracticePoolVersion,
    blueprint: StandardPackVersion,
    items: list[dict[str, Any]],
    *,
    mode: str = "ORIGINAL_SYNTHETIC_TEST_ONLY",
) -> dict[str, Any]:
    return {
        "pool_version_id": str(pool.id),
        "version_label": pool.version_label,
        "scope": pool.scope,
        "jurisdiction": pool.jurisdiction,
        "organisation_id": str(pool.organisation_id),
        "practice_pack_version_id": str(pool.practice_pack_version_id),
        "blueprint_version_id": str(blueprint.id),
        "blueprint_checksum": blueprint.configuration_checksum,
        "origin": mode,
        "items": sorted(items, key=lambda item: item["item_version_id"]),
    }


def select_items(pool: LidPracticePoolVersion, seed: str, count: int) -> list[dict[str, Any]]:
    items = pool.manifest["items"]
    if len(items) < count or len({item["item_id"] for item in items}) != len(items):
        raise ValueError(CONTENT_UNAVAILABLE)
    ranked = sorted(
        items,
        key=lambda item: (
            hashlib.sha256(f"{seed}:{pool.scope}:{item['item_version_id']}".encode()).hexdigest(),
            item["item_version_id"],
        ),
    )
    return ranked[:count]


def form_manifest(
    session: Session,
    exam: ExamVersion,
    pack: StandardPackVersion,
    blueprint: StandardPackVersion,
    general: LidPracticePoolVersion,
    state: LidPracticePoolVersion,
    seed: str,
    *,
    historical: bool = False,
) -> dict[str, Any]:
    from app.exams.service import ExamStudioService
    from app.lid_editorial.reviews import lock_editorial_source_roots
    from app.standard_packs.lid_service import lid_draft_integrity
    from app.standard_packs.service import StandardPackService

    organisation_id = require_technical_pack(pack, exam)
    original = is_lid_editorial_pack(pack)
    if (
        not is_lid_draft_pack(blueprint)
        or (not (original and historical) and lid_draft_integrity(session, blueprint) is not None)
        or pack.configuration_checksum != StandardPackService.checksum(pack)
        or exam.checksum != _checksum(ExamStudioService._payload(session, exam))
        or general.scope != GENERAL
        or general.jurisdiction != "DE"
        or state.scope != STATE
        or state.jurisdiction not in LID_STATES
        or (original and state.jurisdiction != "DE-BE")
        or len(exam.modules) != 1
    ):
        raise ValueError(INVALID_FORM)
    if original and not historical:
        lock_editorial_source_roots(session)

    module = exam.modules[0]
    if (
        len(pack.modules) != 1
        or module.standard_pack_module_id != pack.modules[0].id
        or pack.modules[0].time_limit_seconds != DURATION_SECONDS
        or len(module.sections) != 2
        or any(len(section.tasks) != 1 for section in module.sections)
    ):
        raise ValueError(INVALID_FORM)

    pool_items: list[dict[str, Any]] = []
    selected_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section, pool, count in zip(
        module.sections, (general, state), (GENERAL_COUNT, STATE_COUNT), strict=True
    ):
        task = section.tasks[0]
        if (
            pool.organisation_id != organisation_id
            or pool.practice_pack_version_id != pack.id
            or pool.checksum != _checksum(pool.manifest)
        ):
            raise ValueError(INVALID_FORM)
        spec = session.get(StandardPackTaskSpecification, task.standard_pack_task_spec_id)
        if (
            spec is None
            or spec.section_id != section.standard_pack_section_id
            or spec.section.module_id != module.standard_pack_module_id
            or spec.code != pool.scope
            or spec.min_items != count
            or spec.max_items != count
            or spec.response_mode is not PackResponseMode.SELECT_ONE
            or len(task.item_bindings) != count
        ):
            raise ValueError(INVALID_FORM)

        entries = pool.manifest.get("items")
        if not isinstance(entries, list):
            raise ValueError(INVALID_FORM)
        ids = [uuid.UUID(entry["item_version_id"]) for entry in entries]
        versions = {
            item.id: item
            for item in session.scalars(
                select(QuestionBankItemVersion).where(QuestionBankItemVersion.id.in_(ids))
            )
        }
        eligible_ids = set(
            session.scalars(
                select(QuestionBankStandardPackBinding.item_version_id).where(
                    QuestionBankStandardPackBinding.item_version_id.in_(ids),
                    QuestionBankStandardPackBinding.standard_pack_version_id == pack.id,
                    QuestionBankStandardPackBinding.task_spec_id == spec.id,
                    QuestionBankStandardPackBinding.validation_status == "PASS",
                )
            )
        )
        cefr_item = session.scalar(
            select(QuestionBankItemCefrLevel.id)
            .where(QuestionBankItemCefrLevel.item_version_id.in_(ids))
            .limit(1)
        )
        if set(ids) != set(versions) or set(ids) != eligible_ids or cefr_item is not None:
            raise ValueError(INVALID_FORM)
        verified_items = [
            original_item_pin(session, versions[item_id], organisation_id, historical=historical)
            if original
            else item_pin(
                versions[item_id],
                organisation_id,
                topic=entry["topic"],
                fact_reference=entry["fact_reference"],
            )
            for item_id, entry in zip(ids, entries, strict=True)
        ]
        if original and any(
            entry["scope"] != pool.scope or entry["jurisdiction"] != pool.jurisdiction
            for entry in verified_items
        ):
            raise ValueError(INVALID_FORM)
        origin = ORIGINAL_MODE if original else "ORIGINAL_SYNTHETIC_TEST_ONLY"
        if pool.manifest != pool_manifest(pool, blueprint, verified_items, mode=origin):
            raise ValueError(INVALID_FORM)
        pool_items.extend(verified_items)
        selection = select_items(pool, seed, count)
        for index, (binding, expected) in enumerate(
            zip(task.item_bindings, selection, strict=True)
        ):
            if (
                binding.slot_index != index
                or str(binding.question_bank_item_version_id) != expected["item_version_id"]
                or expected["item_id"] in seen
            ):
                raise ValueError(INVALID_FORM)
            seen.add(expected["item_id"])
            selected_items.append({"binding_id": str(binding.id), "scope": pool.scope, **expected})
    if len({entry["item_id"] for entry in pool_items}) != len(pool_items):
        raise ValueError(INVALID_FORM)
    return {
        "exam_version_id": str(exam.id),
        "exam_version_checksum": exam.checksum,
        "practice_pack_version_id": str(pack.id),
        "practice_pack_checksum": pack.configuration_checksum,
        "blueprint_version_id": str(blueprint.id),
        "blueprint_checksum": blueprint.configuration_checksum,
        "general_pool_version_id": str(general.id),
        "general_pool_checksum": general.checksum,
        "state_pool_version_id": str(state.id),
        "state_pool_checksum": state.checksum,
        "bundesland": state.jurisdiction,
        "selection_version": SELECTION_VERSION,
        "selection_seed": seed,
        "duration_seconds": DURATION_SECONDS,
        "items": selected_items,
        **({"mode": ORIGINAL_MODE} if original else {}),
    }


def require_form(
    session: Session, exam: ExamVersion, pack: StandardPackVersion, *, historical: bool = False
) -> LidPracticeForm:
    form = session.get(LidPracticeForm, exam.id)
    if form is None or form.selection_version != SELECTION_VERSION:
        raise ValueError(INVALID_FORM)
    blueprint = session.get(StandardPackVersion, form.blueprint_version_id)
    general = session.get(LidPracticePoolVersion, form.general_pool_version_id)
    state = session.get(LidPracticePoolVersion, form.state_pool_version_id)
    if (
        blueprint is None
        or general is None
        or state is None
        or form.bundesland != state.jurisdiction
    ):
        raise ValueError(INVALID_FORM)
    try:
        actual = form_manifest(
            session,
            exam,
            pack,
            blueprint,
            general,
            state,
            form.selection_seed,
            historical=historical,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(INVALID_FORM) from exc
    if form.manifest != actual or form.checksum != _checksum(actual):
        raise ValueError(INVALID_FORM)
    if is_lid_editorial_pack(pack):
        assert_original_publication(session, form)
    return form


def assert_original_publication(session: Session, form: LidPracticeForm) -> None:
    from app.lid_editorial.collections import verify_pack
    from app.lid_editorial.reviews import publication_review_pins_valid
    from app.writing_assessment.service import canonical_checksum

    publication = session.scalar(
        select(LidEditorialPublication).where(
            LidEditorialPublication.exam_version_id == form.exam_version_id,
            LidEditorialPublication.action == "PUBLISHED",
        )
    )
    unavailable = "LID_EDITORIAL_PRACTICE_NOT_PUBLISHED"
    if not is_original_form(form) or publication is None:
        raise ValueError(unavailable)
    collection = session.get(LidEditorialCollection, publication.collection_id)
    approval = session.scalar(
        select(LidEditorialPublication).where(
            LidEditorialPublication.collection_id == publication.collection_id,
            LidEditorialPublication.action == "APPROVED",
        )
    )
    if collection is None or approval is None:
        raise ValueError(unavailable)
    try:
        pack = verify_pack(session, collection)
        expected = approval.manifest
        receipt_items = expected["items"]
        form_items = {item["item_version_id"]: item for item in form.manifest["items"]}
        collection_checksum = canonical_checksum(
            {
                "collection_checksum": collection.checksum,
                "items": [
                    {"id": item["item_version_id"], "checksum": item["provenance_checksum"]}
                    for item in receipt_items
                ],
            }
        )
        release_manifest = {
            **expected,
            "pack_approval_id": str(approval.id),
            "pack_approval_checksum": approval.checksum,
            "exam_version_id": str(form.exam_version_id),
            "form_checksum": form.checksum,
            "general_pool_version_id": str(form.general_pool_version_id),
            "state_pool_version_id": str(form.state_pool_version_id),
        }
        if (
            approval.exam_version_id is not None
            or publication.actor_user_id in {approval.actor_user_id, collection.created_by_user_id}
            or approval.actor_user_id == collection.created_by_user_id
            or approval.checksum != canonical_checksum(expected)
            or approval.eligibility_checksum != approval.checksum
            or publication.eligibility_checksum != approval.checksum
            or publication.manifest != release_manifest
            or publication.checksum != canonical_checksum(release_manifest)
            or form.checksum != _checksum(form.manifest)
            or canonical_checksum(collection.manifest) != collection.checksum
            or expected["mode"] != ORIGINAL_MODE
            or expected["collection_id"] != str(collection.id)
            or expected["collection_checksum"] != collection_checksum
            or expected["standard_pack_version_id"] != str(pack.id)
            or expected["standard_pack_checksum"] != pack.configuration_checksum
            or form.manifest["practice_pack_version_id"] != str(pack.id)
            or form.manifest["practice_pack_checksum"] != pack.configuration_checksum
            or expected["blueprint_version_id"] != str(collection.blueprint_version_id)
            or form.blueprint_version_id != collection.blueprint_version_id
            or expected["blueprint_checksum"] != form.manifest["blueprint_checksum"]
            or expected["blueprint_checksum"] != collection.manifest["blueprint_checksum"]
            or len(receipt_items) != GENERAL_COUNT + STATE_COUNT
            or len(form.manifest["items"]) != len(receipt_items)
            or len(form_items) != len(receipt_items)
            or {item["item_version_id"] for item in receipt_items} != form_items.keys()
        ):
            raise ValueError(unavailable)
        for item in receipt_items:
            selected = form_items[item["item_version_id"]]
            if (
                any(
                    selected[key] != item[key]
                    for key in ("item_id", "item_checksum", "key_checksum", "scope", "jurisdiction")
                )
                or selected["editorial_provenance_checksum"] != item["provenance_checksum"]
                or selected["fact_reference"] != item["source"]["version_id"]
            ):
                raise ValueError(unavailable)
        if not publication_review_pins_valid(session, receipt_items):
            raise ValueError(unavailable)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(unavailable) from exc


def assignment_visible(session: Session, form: LidPracticeForm | None) -> bool:
    if form is None:
        return True
    if not is_original_form(form):
        return technical_delivery_enabled()
    try:
        assert_original_publication(session, form)
    except ValueError:
        return False
    return True


def assignment_details(bundesland: str, *, original: bool = False) -> dict[str, Any]:
    return {
        "title": TITLE,
        **({"notice": LID_ORIGINAL_NOTICE, "mode": ORIGINAL_MODE} if original else {}),
        "bundeslaender": [
            {"code": code, "label": label, "available": code == bundesland}
            for code, label in LID_STATES.items()
        ],
    }


def plan_details(form: LidPracticeForm, attempt_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "title": TITLE,
        "notice": LID_ORIGINAL_NOTICE if is_original_form(form) else DISCLOSURE,
        "bundesland": form.bundesland,
        "bundesland_label": LID_STATES[form.bundesland],
        "general_count": GENERAL_COUNT,
        "state_count": STATE_COUNT,
        "form_checksum": form.checksum,
        "attempt_id": str(attempt_id) if attempt_id is not None else None,
        **({"mode": ORIGINAL_MODE} if is_original_form(form) else {}),
    }


def safe_item(item: QuestionBankItemVersion, scope: str) -> dict[str, Any]:
    content = MultipleChoiceContent.model_validate(item.content)
    return {
        "item_version_id": str(item.id),
        "version_label": item.version_label,
        "assessment_family": item.assessment_family.value,
        "language": item.language,
        "task_type": item.task_type.value,
        "response_mode": item.response_mode.value,
        "media_requirement": item.media_requirement.value,
        "lid_pool_scope": scope,
        "content": {
            "prompt": content.prompt,
            "options": [
                {"option_id": option.option_id, "display_text": option.display_text}
                for option in content.options
            ],
        },
    }


def require_attempt_form(
    session: Session, attempt: CandidateExamAttempt, pack: StandardPackVersion
) -> LidPracticeForm:
    exam = session.get(ExamVersion, attempt.exam_version_id)
    if exam is None:
        raise ValueError(INVALID_FORM)
    form = require_form(session, exam, pack, historical=is_lid_editorial_pack(pack))
    plan = attempt.runtime_plan
    if (
        plan.get("lid_practice") != plan_details(form, attempt.id)
        or plan.get("exam_version_id") != str(exam.id)
        or plan.get("standard_pack_version_id") != str(pack.id)
        or plan.get("duration_seconds") != DURATION_SECONDS
        or attempt.organisation_id != exam.exam.organisation_id
        or attempt.delivery_manifest_checksum != attempt.runtime_plan_checksum
        or _checksum(plan) != attempt.runtime_plan_checksum
    ):
        raise ValueError(INVALID_FORM)
    return form


def validate_scoring_snapshot(
    form: LidPracticeForm,
    attempt: CandidateExamAttempt,
    rows: list[dict[str, Any]],
    item_map: dict[str, QuestionBankItemVersion],
) -> int:
    expected = form.manifest["items"]
    if len(rows) != len(expected):
        raise ValueError(INVALID_FORM)
    answered = 0
    for row, pin in zip(rows, expected, strict=True):
        version_id = row["question_bank_item_version_id"]
        if (
            row["binding_id"] != pin["binding_id"]
            or version_id != pin["item_version_id"]
            or version_id not in item_map
        ):
            raise ValueError(INVALID_FORM)
        payload = row["payload"]
        if payload is not None:
            options = MultipleChoiceContent.model_validate(item_map[version_id].content).options
            if set(payload) != {"option_id"} or payload["option_id"] not in {
                option.option_id for option in options
            }:
                raise ValueError(INVALID_FORM)
            answered += 1
    if answered != len(attempt.responses):
        raise ValueError(INVALID_FORM)
    return answered


_CONTEXT_FIELDS = (
    "purpose",
    "threshold",
    "total_item_count",
    "raw_correct_count",
    "numerical_outcome",
    "reason",
    "rule_version_id",
    "rule_version_label",
    "rule_checksum",
    "legal_effective_from",
    "legal_effective_date",
    "eligibility_status",
)
_CITATION_FIELDS = (
    "source_version_id",
    "source_metadata_checksum",
    "source_url",
    "reference_url",
    "locator",
)


def safe_contexts(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bibliographic rule pins for tenant results, with private review decisions omitted."""
    return [
        {
            **{key: row[key] for key in _CONTEXT_FIELDS if key in row},
            "citations": [
                {key: citation[key] for key in _CITATION_FIELDS if key in citation}
                for citation in row.get("citations", [])
            ],
        }
        for row in contexts
    ]


def practice_result(session: Session, attempt: CandidateExamAttempt) -> dict[str, Any] | None:
    from app.models import AssessmentResult, ResultVersion, ScoringRun
    from app.models.enums import CandidateAttemptStatus, ResultOutcome, ScoringRunStatus
    from app.standard_packs.lid_service import lid_draft_integrity

    practice = attempt.runtime_plan.get("lid_practice")
    if (
        not isinstance(practice, dict)
        or (practice.get("mode") != ORIGINAL_MODE and not technical_delivery_enabled())
        or practice.get("attempt_id") != str(attempt.id)
    ):
        raise ValueError("LID_PRACTICE_NOT_APPLICABLE")
    if attempt.status is not CandidateAttemptStatus.SUBMITTED:
        return None
    form = session.get(LidPracticeForm, attempt.exam_version_id)
    if form is None or form.checksum != practice.get("form_checksum"):
        raise ValueError(INVALID_FORM)
    if is_original_form(form):
        assert_original_publication(session, form)
    row = session.execute(
        select(ResultVersion, ScoringRun)
        .join(AssessmentResult, ResultVersion.result_id == AssessmentResult.id)
        .join(ScoringRun, ScoringRun.id == ResultVersion.scoring_run_id)
        .where(
            AssessmentResult.attempt_id == attempt.id,
            AssessmentResult.organisation_id == attempt.organisation_id,
            AssessmentResult.candidate_user_id == attempt.candidate_user_id,
            ScoringRun.attempt_id == attempt.id,
            ScoringRun.organisation_id == attempt.organisation_id,
            ScoringRun.candidate_user_id == attempt.candidate_user_id,
            ScoringRun.status == ScoringRunStatus.COMPLETED,
        )
        .order_by(ResultVersion.version_number.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    version, run = row
    preview = version.rule_provenance.get("lid_practice_preview")
    if (
        not isinstance(preview, dict)
        or preview.get("form_checksum") != form.checksum
        or version.outcome is not ResultOutcome.UNDETERMINED
        or version.raw_awarded_points is None
        or version.raw_maximum_points != GENERAL_COUNT + STATE_COUNT
    ):
        raise ValueError(INVALID_FORM)
    blueprint = session.get(StandardPackVersion, form.blueprint_version_id)
    stale_reason = (
        lid_draft_integrity(session, blueprint)
        if blueprint is not None
        else "LID_SOURCE_OR_RULE_UNAVAILABLE"
    )
    contexts = safe_contexts(preview["contexts"])
    if stale_reason:
        contexts = [
            {**context, "numerical_outcome": "UNDETERMINED", "reason": stale_reason}
            for context in contexts
        ]
    return {
        "scoring_run_id": str(run.id),
        "result_version_id": str(version.id),
        "raw_correct": int(version.raw_awarded_points),
        "total_items": int(version.raw_maximum_points),
        "answered_count": preview["answered_count"],
        "bundesland": form.bundesland,
        "contexts": contexts,
        "official_outcome": "UNDETERMINED",
        "administrative_recognition": "NOT_ESTABLISHED",
        "certification": "NOT_CERTIFIED",
    }
