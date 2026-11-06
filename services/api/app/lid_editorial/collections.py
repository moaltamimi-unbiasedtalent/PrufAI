"""Persist the original Berlin draft set using native item and pack versions."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any, Final
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.lid_editorial.access import editorial_preview_enabled
from app.lid_editorial.content import (
    ORIGINAL_DRAFTS,
    editorial_replacement_source,
    editorial_source_snapshot,
    editorial_source_version,
    register_editorial_source,
)
from app.lid_policy import (
    LID_EDITORIAL_ITEM_CODE_PREFIX,
    LID_EDITORIAL_PACK_CODE_PREFIX,
    LID_EDITORIAL_SCHEMA,
    LID_EXAM_FAMILY,
    LID_ORIGINAL_NOTICE,
    LID_STATES,
    LID_STRUCTURE,
    is_lid_editorial_pack,
)
from app.lid_practice import service as lid
from app.models import QuestionBankItem, QuestionBankItemVersion, StandardPack, StandardPackVersion
from app.models.enums import (
    AssessmentFamily,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankTaskType,
    StandardPackStatus,
)
from app.models.lid_editorial import LidEditorialCollection, LidEditorialItemVersion
from app.models.lid_practice import LidPracticePoolVersion
from app.models.question_bank import QuestionBankItemEvidenceBinding
from app.question_bank.schemas import MultipleChoiceContent
from app.question_bank.service import QuestionBankService, normalized_fingerprint
from app.standard_packs.lid_service import build_lid_structure, lid_draft_integrity
from app.standard_packs.lid_sources import lid_berlin_date
from app.standard_packs.service import StandardPackService
from app.writing_assessment.service import canonical_checksum

CODE = "BERLIN_ORIGINAL_PRACTICE_1"
BERLIN = "DE-BE"
MODE: Final = "CONTENT_EDITORIAL_PREVIEW"
GENUINE: Final = "PUBLISHED_PRACTICE"
AUTHORSHIP = "AI_ASSISTED_DRAFT"
COLLECTION_LOCK = 6010040


SOURCE_REVISION_BLOCKERS = frozenset(
    {
        "LID_EDITORIAL_SOURCE_RECHECK_REQUIRED",
        "LID_EDITORIAL_SOURCE_SUCCESSOR_REVIEWED",
        "LID_EDITORIAL_SOURCE_WITHDRAWN",
        "LID_EDITORIAL_REVIEWED_METADATA_SOURCE_REQUIRED",
        "LID_EDITORIAL_SOURCE_RIGHTS_CHANGED",
    }
)


class SourceReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version_id: uuid.UUID
    metadata_checksum: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reference_url: str = Field(min_length=10, max_length=1000)
    locator: str = Field(min_length=3, max_length=400)
    fact: str = Field(min_length=10, max_length=1500)
    last_verified: date
    recheck_after: date


def source_revision_required(session: Session, version: QuestionBankItemVersion) -> bool:
    from app.lid_editorial.reviews import current_item_evidence

    if version.status not in {
        QuestionBankLifecycleStatus.HUMAN_REVIEW,
        QuestionBankLifecycleStatus.APPROVED,
    }:
        return False
    blockers = current_item_evidence(session, version, mode=GENUINE, lock_for_review=False)[
        "blockers"
    ]
    return bool(SOURCE_REVISION_BLOCKERS.intersection(blockers))


def _replace_source(
    session: Session,
    source: dict[str, Any],
    claim: dict[str, Any],
    replacement: SourceReplacement,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = source["source_key"]
    previous_id = uuid.UUID(source["version_id"])
    if replacement.version_id == previous_id:
        if replacement.metadata_checksum != source["metadata_checksum"]:
            raise ValueError("LID_EDITORIAL_EXACT_SOURCE_PIN_MISMATCH")
        current = editorial_source_version(
            session, key, previous_id, replacement.metadata_checksum, require_reviewed=True
        )
    else:
        current = editorial_replacement_source(
            session, key, previous_id, replacement.version_id, replacement.metadata_checksum
        )
    snapshot = editorial_source_snapshot(key, current)
    today = datetime.now(ZoneInfo("Europe/Berlin")).date()
    if not (
        replacement.last_verified
        <= today
        < replacement.recheck_after
        <= replacement.last_verified + timedelta(days=365)
    ):
        raise ValueError("LID_EDITORIAL_SOURCE_DATE_INVALID")
    url = urlsplit(replacement.reference_url)
    issuer_hosts = {urlsplit(snapshot[field]).hostname for field in ("source_url", "edition_url")}
    if (
        url.scheme != "https"
        or url.hostname not in issuer_hosts
        or url.username is not None
        or url.password is not None
        or url.port not in {None, 443}
    ):
        raise ValueError("LID_EDITORIAL_SOURCE_REPLACEMENT_ISSUER_MISMATCH")
    same_edition = replacement.version_id == previous_id
    return snapshot, {
        **claim,
        "fact": replacement.fact,
        "locator": replacement.locator,
        "reference_url": replacement.reference_url,
        "last_verified": replacement.last_verified.isoformat(),
        "recheck_after": replacement.recheck_after.isoformat(),
        "applicability_as_of": replacement.last_verified.isoformat(),
        "legal_effective_from": claim.get("legal_effective_from")
        if same_edition
        else (lid_berlin_date(current.effective_from)),
        "effective_date_basis": claim.get("effective_date_basis")
        if same_edition
        else ("Exact reviewed Source Registry edition" if current.effective_from else None),
        "effective_date_reference": claim.get("effective_date_reference")
        if same_edition
        else (snapshot["edition_url"] if current.effective_from else None),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def lock_collection(session: Session, collection_id: uuid.UUID) -> LidEditorialCollection:
    row = session.scalar(
        select(LidEditorialCollection)
        .where(LidEditorialCollection.id == collection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError("LID_EDITORIAL_COLLECTION_NOT_FOUND")
    return row


def _audit(
    session: Session,
    context: TenantContext,
    event: str,
    collection: LidEditorialCollection,
    metadata: dict[str, Any],
) -> None:
    AuditService.record(
        session,
        context,
        event_type=f"lid.editorial.{event}",
        entity_type="lid_editorial_collection",
        entity_id=str(collection.id),
        organisation_id=collection.organisation_id,
        require_audit_permission=False,
        metadata=metadata,
    )


def _task_id(pack: StandardPackVersion, scope: str) -> uuid.UUID:
    tasks = [
        task
        for module in pack.modules
        for section in module.sections
        for task in section.tasks
        if task.code == scope
    ]
    if len(tasks) != 1:
        raise ValueError("LID_EDITORIAL_PACK_STRUCTURE_INVALID")
    return tasks[0].id


def validate_question(content: dict[str, Any]) -> MultipleChoiceContent:
    parsed = MultipleChoiceContent.model_validate(content)
    options = [" ".join(option.display_text.split()).casefold() for option in parsed.options]
    if (
        len(options) != 4
        or len(set(options)) != 4
        or not all(options)
        or {option.order for option in parsed.options} != set(range(4))
        or len(parsed.correct_option_ids) != 1
        or parsed.correct_option_ids[0] not in {option.option_id for option in parsed.options}
        or not parsed.prompt.strip()
    ):
        raise ValueError("LID_EDITORIAL_INVALID_ANSWER_OPTIONS")
    return parsed


def question_fingerprint(content: dict[str, Any]) -> str:
    return normalized_fingerprint({"prompt": " ".join(str(content["prompt"]).split()).casefold()})


def _add_version(
    session: Session,
    context: TenantContext,
    collection: LidEditorialCollection,
    pack: StandardPackVersion,
    item: QuestionBankItem,
    *,
    label: str,
    content: dict[str, Any],
    scope: str,
    jurisdiction: str,
    ordinal: int,
    rationale: str,
    source: dict[str, Any],
    claim: dict[str, Any],
    topic: str,
    previous: QuestionBankItemVersion | None = None,
    successor_operation_id: uuid.UUID | None = None,
    request_checksum: str | None = None,
) -> LidEditorialItemVersion:
    from app.lid_editorial.reviews import answer_key_checksum

    validate_question(content)
    args: dict[str, Any] = {
        "assessment_family": AssessmentFamily.INTEGRATION_KNOWLEDGE,
        "exam_family": LID_EXAM_FAMILY,
        "language": "de",
        "task_type": QuestionBankTaskType.MULTIPLE_CHOICE,
        "response_mode": PackResponseMode.SELECT_ONE,
        "max_points": Decimal("1.0000"),
        "scoring_metadata": lid.OBJECTIVE_POLICY,
        "rights_status": QuestionBankRightsStatus.UNKNOWN,
    }
    version = QuestionBankService.create_version(
        session,
        context,
        item,
        version_label=label,
        content=content,
        origin_type=QuestionBankOriginType.AI_PROPOSED,
        editorial_initial=previous is None,
        editorial_successor_of=previous,
        **args,
    )
    if not version.checksum:
        raise ValueError("LID_EDITORIAL_ITEM_CHECKSUM_REQUIRED")
    provenance = {
        "collection_id": str(collection.id),
        "item_id": str(item.id),
        "item_version_id": str(version.id),
        "item_checksum": version.checksum,
        "key_checksum": answer_key_checksum(content),
        "authorship": AUTHORSHIP,
        "authoring_origin": "CODEX_ASSISTED_ORIGINAL_DRAFT",
        "rights_status": "RIGHTS_REVIEW_REQUIRED",
        "rights_holder": None,
        "source_dependency": "BIBLIOGRAPHIC_FACT_REFERENCE_ONLY",
        "source": source,
        "claim": claim,
        "rationale": rationale,
        "scope": scope,
        "jurisdiction": jurisdiction,
        "topic": topic,
        "ordinal": ordinal,
        "supersedes_version_id": str(previous.id) if previous else None,
        "successor_operation_id": str(successor_operation_id) if successor_operation_id else None,
        "request_checksum": request_checksum,
    }
    extension = LidEditorialItemVersion(
        item_version_id=version.id,
        item_id=item.id,
        collection_id=collection.id,
        ordinal=ordinal,
        scope=scope,
        jurisdiction=jurisdiction,
        provenance=provenance,
        checksum=canonical_checksum(provenance),
        created_at=_now(),
    )
    session.add(extension)
    session.flush()
    QuestionBankService.bind_pack(session, context, version, pack.id, _task_id(pack, scope))
    session.add(
        QuestionBankItemEvidenceBinding(
            item_version_id=version.id,
            knowledge_document_version_id=uuid.UUID(source["version_id"]),
            evidence_id=canonical_checksum(claim),
            purpose="S4_ORIGINAL_PRACTICE_BIBLIOGRAPHIC_FACT",
        )
    )
    session.flush()
    return extension


def create_collection(
    session: Session,
    context: TenantContext,
    platform: TenantContext,
    *,
    blueprint_version_id: uuid.UUID,
    expected_blueprint_checksum: str,
    operation_id: uuid.UUID,
) -> LidEditorialCollection:
    org = context.organisation_id
    if org is None or context.membership_id is None:
        raise ValueError("LID_EDITORIAL_ORGANISATION_SCOPE_REQUIRED")
    AuthorizationService.require_permission(context, "question_bank.manage", org)
    AuthorizationService.require_permission(context, "standard_pack.manage", org)
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": COLLECTION_LOCK})
    old = session.scalar(
        select(LidEditorialCollection).where(LidEditorialCollection.operation_id == operation_id)
    )
    if old is not None:
        if (
            old.organisation_id != org
            or old.created_by_user_id != context.user_id
            or old.blueprint_version_id != blueprint_version_id
            or old.manifest["blueprint_checksum"] != expected_blueprint_checksum
        ):
            raise ValueError("LID_EDITORIAL_OPERATION_CONFLICT")
        return old
    if (
        session.scalar(
            select(LidEditorialCollection.id).where(
                LidEditorialCollection.organisation_id == org, LidEditorialCollection.code == CODE
            )
        )
        is not None
    ):
        raise ValueError("LID_EDITORIAL_COLLECTION_EXISTS")
    blueprint = session.get(StandardPackVersion, blueprint_version_id)
    if (
        blueprint is None
        or blueprint.configuration_checksum != expected_blueprint_checksum
        or lid_draft_integrity(session, blueprint) is not None
    ):
        raise ValueError("LID_EDITORIAL_CURRENT_BLUEPRINT_REQUIRED")
    scopes = [item.scope for item in ORIGINAL_DRAFTS]
    if scopes.count(lid.GENERAL) != 30 or scopes.count(lid.STATE) != 3 or len(scopes) != 33:
        raise ValueError("LID_EDITORIAL_DRAFT_INVENTORY_INVALID")
    fingerprints = set()
    for item_draft in ORIGINAL_DRAFTS:
        candidate = item_draft.question_content()
        validate_question(candidate)
        fingerprint = question_fingerprint(candidate)
        if fingerprint in fingerprints:
            raise ValueError("LID_EDITORIAL_DUPLICATE_ITEM")
        fingerprints.add(fingerprint)
    sources = {
        key: editorial_source_snapshot(key, register_editorial_source(session, platform, key))
        for key in sorted({item.claim.source_key for item in ORIGINAL_DRAFTS})
    }
    now = _now()
    identity = StandardPack(
        code=LID_EDITORIAL_PACK_CODE_PREFIX + org.hex.upper(),
        name="Leben in Deutschland — Original Berlin practice",
        description=LID_ORIGINAL_NOTICE,
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family=LID_EXAM_FAMILY,
        language="de",
        status=StandardPackStatus.DRAFT,
    )
    pack = StandardPackVersion(
        standard_pack=identity,
        version_label="S4 original Berlin editorial draft 1",
        status=StandardPackStatus.DRAFT,
        configuration_schema_version=LID_EDITORIAL_SCHEMA,
        supported_levels=[],
        allowed_classifications=["PRACTICE"],
        notes=LID_ORIGINAL_NOTICE,
        scoring_configuration_ref="objective:select-one:one-point:v1",
        result_configuration_ref=f"lid-draft-preview:{expected_blueprint_checksum}",
        created_at=now,
        created_by_user_id=context.user_id,
    )
    session.add(pack)
    build_lid_structure(pack, now)
    session.flush()
    pack.configuration_checksum = StandardPackService.checksum(pack)
    session.flush()  # Persist the initial checksum before the editorial collection seals it.
    manifest = {
        "code": CODE,
        "organisation_id": str(org),
        "pack_version_id": str(pack.id),
        "pack_checksum": pack.configuration_checksum,
        "blueprint_version_id": str(blueprint.id),
        "blueprint_checksum": expected_blueprint_checksum,
        "authoring_origin": AUTHORSHIP,
        "jurisdiction": BERLIN,
        "supported_levels": [],
        "classification": "PRACTICE",
        "structure": LID_STRUCTURE,
        "selection_version": lid.SELECTION_VERSION,
        "initial_draft_codes": [item.code for item in ORIGINAL_DRAFTS],
    }
    collection = LidEditorialCollection(
        organisation_id=org,
        pack_version_id=pack.id,
        blueprint_version_id=blueprint.id,
        code=CODE,
        operation_id=operation_id,
        created_by_user_id=context.user_id,
        created_at=now,
        manifest=manifest,
        checksum=canonical_checksum(manifest),
    )
    session.add(collection)
    session.flush()
    for ordinal, draft in enumerate(ORIGINAL_DRAFTS, start=1):
        item = QuestionBankService.create_item(
            session,
            context,
            code=LID_EDITORIAL_ITEM_CODE_PREFIX + draft.code,
            scope_type="ORGANISATION_PRIVATE",
            organisation_id=org,
            editorial_initial=True,
        )
        _add_version(
            session,
            context,
            collection,
            pack,
            item,
            label="original-draft-v1",
            content=draft.question_content(),
            scope=draft.scope,
            jurisdiction=draft.jurisdiction,
            ordinal=ordinal,
            rationale=draft.rationale,
            source=sources[draft.claim.source_key],
            claim=draft.claim.public(),
            topic=draft.topic,
        )
    _audit(
        session,
        context,
        "collection_created",
        collection,
        {
            "operation_id": str(operation_id),
            "pack_version_id": str(pack.id),
            "item_count": 33,
        },
    )
    return collection


def current_versions(
    session: Session, collection: LidEditorialCollection
) -> list[tuple[LidEditorialItemVersion, QuestionBankItemVersion]]:
    records = list(
        session.execute(
            select(LidEditorialItemVersion, QuestionBankItemVersion)
            .join(
                QuestionBankItemVersion,
                QuestionBankItemVersion.id == LidEditorialItemVersion.item_version_id,
            )
            .where(LidEditorialItemVersion.collection_id == collection.id)
            .order_by(LidEditorialItemVersion.ordinal, LidEditorialItemVersion.item_version_id)
        ).tuples()
    )
    superseded = {version.supersedes_version_id for _, version in records}
    rows = [(extension, version) for extension, version in records if version.id not in superseded]
    if len({version.item_id for _, version in rows}) != len(rows):
        raise ValueError("LID_EDITORIAL_LINEAGE_CONFLICT")
    return rows


def current_checksum(
    collection: LidEditorialCollection,
    rows: list[tuple[LidEditorialItemVersion, QuestionBankItemVersion]],
) -> str:
    if canonical_checksum(collection.manifest) != collection.checksum:
        raise ValueError("LID_EDITORIAL_COLLECTION_PROVENANCE_INVALID")
    return canonical_checksum(
        {
            "collection_checksum": collection.checksum,
            "items": [
                {"id": str(version.id), "checksum": extension.checksum}
                for extension, version in rows
            ],
        }
    )


def verify_pack(session: Session, collection: LidEditorialCollection) -> StandardPackVersion:
    pack = session.get(StandardPackVersion, collection.pack_version_id)
    if (
        pack is None
        or not is_lid_editorial_pack(pack)
        or pack.standard_pack.code
        != LID_EDITORIAL_PACK_CODE_PREFIX + collection.organisation_id.hex.upper()
        or pack.configuration_checksum != collection.manifest["pack_checksum"]
        or StandardPackService.checksum(pack) != pack.configuration_checksum
        or pack.rule_bindings
    ):
        raise ValueError("LID_EDITORIAL_PACK_PROVENANCE_INVALID")
    return pack


def successor(
    session: Session,
    context: TenantContext,
    collection: LidEditorialCollection,
    version_id: uuid.UUID,
    *,
    expected_checksum: str,
    operation_id: uuid.UUID,
    prompt: str,
    options: list[str],
    correct_index: int,
    rationale: str,
    replacement_source: SourceReplacement | None = None,
) -> LidEditorialItemVersion:
    collection = lock_collection(session, collection.id)
    pack = verify_pack(session, collection)
    if pack.status is not StandardPackStatus.DRAFT:
        raise ValueError("LID_EDITORIAL_PACK_EDITION_IMMUTABLE")
    AuthorizationService.require_permission(
        context, "question_bank.manage", collection.organisation_id
    )
    base = session.get(LidEditorialItemVersion, version_id)
    previous = session.get(QuestionBankItemVersion, version_id)
    if base is None or base.collection_id != collection.id or previous is None:
        raise ValueError("LID_EDITORIAL_ITEM_NOT_FOUND")
    request = canonical_checksum(
        {
            "previous": str(version_id),
            "expected": expected_checksum,
            "prompt": prompt,
            "options": options,
            "correct_index": correct_index,
            "rationale": rationale,
            "replacement_source": replacement_source.model_dump(mode="json")
            if replacement_source
            else None,
        }
    )
    records = list(
        session.scalars(
            select(LidEditorialItemVersion).where(
                LidEditorialItemVersion.collection_id == collection.id,
            )
        )
    )
    for row in records:
        if row.provenance.get("successor_operation_id") == str(operation_id):
            existing = session.get(QuestionBankItemVersion, row.item_version_id)
            if (
                existing is None
                or existing.created_by_user_id != context.user_id
                or row.provenance.get("request_checksum") != request
            ):
                raise ValueError("LID_EDITORIAL_OPERATION_CONFLICT")
            return row
    rows = current_versions(session, collection)
    if (
        base.checksum != expected_checksum
        or not any(item.id == version_id for _, item in rows)
        or (
            previous.status is not QuestionBankLifecycleStatus.REJECTED
            and (replacement_source is None or not source_revision_required(session, previous))
        )
    ):
        raise ValueError("LID_EDITORIAL_REJECTED_CURRENT_VERSION_REQUIRED")
    content = {
        "kind": "MULTIPLE_CHOICE",
        "prompt": prompt,
        "options": [
            {"option_id": f"option-{index + 1}", "display_text": value, "order": index}
            for index, value in enumerate(options)
        ],
        "correct_option_ids": [f"option-{correct_index + 1}"],
    }
    validate_question(content)
    fingerprint = question_fingerprint(content)
    if any(
        item.item_id != previous.item_id and question_fingerprint(item.content) == fingerprint
        for _, item in rows
    ):
        raise ValueError("LID_EDITORIAL_DUPLICATE_ITEM")
    original = base.provenance
    source = original["source"]
    claim = original["claim"]
    if replacement_source is not None:
        source, claim = _replace_source(session, source, claim, replacement_source)
    else:
        editorial_source_version(
            session,
            source["source_key"],
            uuid.UUID(source["version_id"]),
            source["metadata_checksum"],
        )
    number = 1 + sum(row.item_id == previous.item_id for row in records)
    row = _add_version(
        session,
        context,
        collection,
        pack,
        previous.item,
        label=f"original-draft-v{number}",
        content=content,
        scope=base.scope,
        jurisdiction=base.jurisdiction,
        ordinal=base.ordinal,
        rationale=rationale,
        source=source,
        claim=claim,
        topic=original["topic"],
        previous=previous,
        successor_operation_id=operation_id,
        request_checksum=request,
    )
    _audit(
        session,
        context,
        "item_successor_created",
        collection,
        {
            "item_version_id": str(row.item_version_id),
            "previous_item_version_id": str(previous.id),
            "item_checksum": row.provenance["item_checksum"],
            "operation_id": str(operation_id),
        },
    )
    return row


def exact_preview_order(
    collection: LidEditorialCollection,
    rows: list[tuple[LidEditorialItemVersion, QuestionBankItemVersion]],
) -> list[tuple[LidEditorialItemVersion, QuestionBankItemVersion]]:
    if len(rows) != 33 or len({p.ordinal for p, _ in rows}) != 33:
        raise ValueError("PRACTICE_FORM_NOT_READY")
    if len({question_fingerprint(row.content) for _, row in rows}) != 33:
        raise ValueError("LID_EDITORIAL_DUPLICATE_ITEM")
    by_id = {str(row.id): (pin, row) for pin, row in rows}
    ordered: list[tuple[LidEditorialItemVersion, QuestionBankItemVersion]] = []
    for scope, state, count in ((lid.GENERAL, "DE", 30), (lid.STATE, BERLIN, 3)):
        eligible = []
        for pin, row in rows:
            if pin.scope == scope and pin.jurisdiction == state:
                validate_question(row.content)
                if (
                    pin.checksum != canonical_checksum(pin.provenance)
                    or pin.provenance["item_checksum"] != row.checksum
                ):
                    raise ValueError("LID_EDITORIAL_ITEM_PROVENANCE_INVALID")
                eligible.append({"item_id": str(row.item_id), "item_version_id": str(row.id)})
        if len(eligible) != count:
            raise ValueError("PRACTICE_FORM_NOT_READY")
        pool = LidPracticePoolVersion(scope=scope, manifest={"items": eligible})
        selection = lid.select_items(pool, current_checksum(collection, rows), count)
        ordered.extend(by_id[item["item_version_id"]] for item in selection)
    return ordered


def preview_order(
    session: Session, collection: LidEditorialCollection, bundesland: str, expected: str
) -> tuple[str, list[tuple[LidEditorialItemVersion, QuestionBankItemVersion]]]:
    if not editorial_preview_enabled(session):
        raise PermissionError("LID_EDITORIAL_DISPOSABLE_PREVIEW_REQUIRED")
    if bundesland not in LID_STATES:
        raise ValueError("LID_INVALID_BUNDESLAND")
    if bundesland != BERLIN:
        raise ValueError("CONTENT_UNAVAILABLE")
    verify_pack(session, collection)
    rows = current_versions(session, collection)
    checksum = current_checksum(collection, rows)
    if expected != checksum:
        raise ValueError("LID_EDITORIAL_STALE_COLLECTION_CHECKSUM")
    return checksum, exact_preview_order(collection, rows)
