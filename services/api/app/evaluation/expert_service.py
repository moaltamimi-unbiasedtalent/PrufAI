"""Governed human references; never writes candidate assessments or invokes AI."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.evaluation import service as foundation
from app.evaluation.expert_evidence import validate_judgment_evidence, validate_reference_source
from app.evaluation.expert_requests import (
    AdjudicationInput,
    JudgmentInput,
    QualificationInput,
    ReferenceDatasetInput,
)
from app.evaluation.expert_schemas import SourceInput
from app.models import (
    EvaluationDataset,
    EvaluationDatasetVersion,
    RubricVersion,
    SpeakingExecutionJob,
    SpeechTranscriptionJob,
    StandardPackVersion,
    User,
    WritingExecutionJob,
)
from app.models.expert_evaluation import (
    EvaluationAdjudication,
    EvaluationExaminerQualification,
    EvaluationExpertJudgment,
    EvaluationReference,
)
from app.writing_assessment.service import canonical_checksum

LIMITATIONS = [
    "Two independent judgments are not automatically expert consensus.",
    "Synthetic judgments remain SYNTHETIC_TEST; genuine examiner calibration is separate.",
    "Assessment validity and CEFR validity are NOT ESTABLISHED.",
    "Empirical thresholds are UNCONFIGURED; no production activation or candidate scoring.",
]


def _locked(session: Session, model: Any, identifier: uuid.UUID) -> Any:
    row = session.scalar(
        select(model)
        .where(model.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError("EVALUATION_REFERENCE_NOT_FOUND")
    return row


def _access(session: Session, context: TenantContext, row: EvaluationReference) -> None:
    if context.organisation_id is not None:
        raise AuthorizationError("EVALUATION_PLATFORM_CONTEXT_REQUIRED")
    if row.organisation_id:
        scoped = build_tenant_context(session, context.user_id, row.organisation_id)
        permission = (
            "speech_transcription.read" if row.kind == "TRANSCRIPTION" else "subjective.review"
        )
        require_active_tenant_permission(session, scoped, permission, row.organisation_id)
    else:
        foundation.require(session, context, foundation.READ, foundation.REVIEW)


def _source_request(session: Session, row: EvaluationReference) -> SourceInput:
    dataset = session.get(EvaluationDataset, row.dataset_id)
    assert dataset
    return SourceInput(**row.body, operation_id=row.operation_id, code=dataset.code)


def _reference(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    *,
    lock: bool = True,
    revalidate: bool = True,
) -> EvaluationReference:
    row = (
        _locked(session, EvaluationReference, identifier)
        if lock
        else session.get(EvaluationReference, identifier)
    )
    if row is None:
        raise ValueError("EVALUATION_REFERENCE_NOT_FOUND")
    _access(session, context, row)
    if canonical_checksum(row.body) != row.checksum:
        raise foundation.EvaluationConflict("EVALUATION_REFERENCE_CHECKSUM_MISMATCH")
    if revalidate:
        resolved = validate_reference_source(session, context, _source_request(session, row))
        if any(
            resolved[key] != getattr(row, key)
            for key in ("body", "content_checksum", "lineage_checksum", "evidence_class")
        ):
            raise foundation.EvaluationConflict("EVALUATION_REFERENCE_SOURCE_DRIFT")
    return row


def _expected(row: Any, checksum: str) -> None:
    if row.checksum != checksum:
        raise foundation.EvaluationConflict("EVALUATION_REFERENCE_CHECKSUM_MISMATCH")


def _operation(session: Session, operation: uuid.UUID) -> None:
    if session.bind and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": str(operation)}
        )


def create_reference(
    session: Session, context: TenantContext, payload: SourceInput
) -> EvaluationReference:
    trusted = foundation.require(session, context, foundation.MANAGE, foundation.RUN)
    _operation(session, payload.operation_id)
    resolved = validate_reference_source(session, context, payload)
    body, checksum = resolved["body"], canonical_checksum(resolved["body"])
    old = session.scalar(
        select(EvaluationReference).where(EvaluationReference.operation_id == payload.operation_id)
    )
    if old:
        dataset = session.get(EvaluationDataset, old.dataset_id)
        if (
            old.created_by != trusted.user_id
            or old.checksum != checksum
            or not dataset
            or dataset.code != payload.code
        ):
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        _access(session, context, old)
        return old
    dataset = session.scalar(
        select(EvaluationDataset).where(EvaluationDataset.code == payload.code).with_for_update()
    )
    if dataset:
        if (
            dataset.organisation_id != payload.organisation_id
            or dataset.created_by != trusted.user_id
        ):
            raise foundation.EvaluationConflict("EVALUATION_DATASET_SCOPE_CONFLICT")
    else:
        dataset = EvaluationDataset(
            code=payload.code,
            organisation_id=payload.organisation_id,
            created_by=trusted.user_id,
            created_at=foundation.utcnow(),
        )
        session.add(dataset)
        session.flush()
    if session.bind and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('evaluation-split',0))")
        )
    if session.scalar(
        select(EvaluationReference.id).where(
            EvaluationReference.split != payload.split,
            or_(
                EvaluationReference.content_checksum == resolved["content_checksum"],
                EvaluationReference.lineage_checksum == resolved["lineage_checksum"],
            ),
        )
    ):
        raise foundation.EvaluationConflict("EVALUATION_SPLIT_LEAKAGE")
    from app.evaluation.split_integrity import guard_reference

    guard_reference(session, resolved["content_checksum"], payload.split)
    row = EvaluationReference(
        operation_id=payload.operation_id,
        dataset_id=dataset.id,
        organisation_id=payload.organisation_id,
        kind=payload.kind,
        split=payload.split,
        evidence_class=resolved["evidence_class"],
        body=body,
        checksum=checksum,
        content_checksum=resolved["content_checksum"],
        lineage_checksum=resolved["lineage_checksum"],
        status="PENDING_APPROVAL",
        created_by=trusted.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, trusted, "reference_created", row)
    return row


def approve_reference(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    checksum: str,
    review_reference: str,
) -> EvaluationReference:
    trusted = foundation.require(session, context, foundation.MANAGE, foundation.REVIEW)
    row = _reference(session, context, identifier)
    _expected(row, checksum)
    if trusted.user_id == row.created_by:
        raise AuthorizationError("EVALUATION_INDEPENDENT_APPROVAL_REQUIRED")
    if row.status == "APPROVED":
        if row.approved_by == trusted.user_id and row.approval_reference == review_reference:
            return row
        raise foundation.EvaluationConflict("EVALUATION_REFERENCE_ALREADY_APPROVED")
    row.status, row.approved_by, row.approved_at, row.approval_reference = (
        "APPROVED",
        trusted.user_id,
        foundation.utcnow(),
        review_reference,
    )
    session.flush()
    foundation._audit(session, trusted, "reference_rights_approved", row)
    return row


def create_qualification(
    session: Session, context: TenantContext, payload: QualificationInput
) -> EvaluationExaminerQualification:
    trusted = foundation.require(session, context, foundation.MANAGE, foundation.REVIEW)
    _operation(session, payload.operation_id)
    if session.get(User, payload.examiner_id) is None or payload.expires_at <= foundation.utcnow():
        raise ValueError("EVALUATION_EXAMINER_OR_EXPIRY_INVALID")
    examiner = session.get(User, payload.examiner_id)
    if payload.origin == "VERIFIED_CREDENTIAL" and (
        payload.credential_reference.lower().startswith(("synthetic:", "test:", "demo:"))
        or examiner is None
        or examiner.normalized_email.endswith((".invalid", ".test", ".local"))
        or "SYNTHETIC" in (examiner.display_name or "").upper()
    ):
        raise ValueError("EVALUATION_SYNTHETIC_ACTOR_CANNOT_BECOME_EXPERT")
    pack = session.get(StandardPackVersion, payload.standard_pack_version_id)
    if pack is None or pack.configuration_checksum != payload.standard_pack_checksum:
        raise ValueError("EVALUATION_QUALIFICATION_SCOPE_INVALID")
    if payload.rubric_version_id:
        rubric = session.get(RubricVersion, payload.rubric_version_id)
        if (
            rubric is None
            or rubric.checksum != payload.rubric_checksum
            or rubric.standard_pack_version_id != pack.id
            or rubric.skill.value != payload.kind
            or rubric.language != payload.language
        ):
            raise ValueError("EVALUATION_QUALIFICATION_SCOPE_INVALID")
    body = payload.model_dump(mode="json", exclude={"operation_id"})
    checksum = canonical_checksum(body)
    old = session.scalar(
        select(EvaluationExaminerQualification).where(
            EvaluationExaminerQualification.operation_id == payload.operation_id
        )
    )
    if old:
        if old.created_by != trusted.user_id or old.checksum != checksum:
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        return old
    row = EvaluationExaminerQualification(
        operation_id=payload.operation_id,
        examiner_id=payload.examiner_id,
        organisation_id=payload.organisation_id,
        body=body,
        checksum=checksum,
        evidence_class="SYNTHETIC_TEST"
        if payload.origin == "SYNTHETIC_TEST"
        else "VERIFIED_CREDENTIAL",
        status="PENDING_APPROVAL",
        created_by=trusted.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, trusted, "examiner_qualification_submitted", row)
    return row


def approve_qualification(
    session: Session,
    context: TenantContext,
    identifier: uuid.UUID,
    checksum: str,
    review_reference: str,
) -> EvaluationExaminerQualification:
    trusted = foundation.require(session, context, foundation.MANAGE, foundation.REVIEW)
    row = _locked(session, EvaluationExaminerQualification, identifier)
    _expected(row, checksum)
    if trusted.user_id in {row.examiner_id, row.created_by}:
        raise AuthorizationError("EVALUATION_INDEPENDENT_CREDENTIAL_REVIEW_REQUIRED")
    if (
        canonical_checksum(row.body) != row.checksum
        or datetime.fromisoformat(row.body["expires_at"].replace("Z", "+00:00"))
        <= foundation.utcnow()
    ):
        raise foundation.EvaluationConflict("EVALUATION_QUALIFICATION_INVALID")
    if row.status == "APPROVED":
        if row.approved_by == trusted.user_id and row.approval_reference == review_reference:
            return row
        raise foundation.EvaluationConflict("EVALUATION_QUALIFICATION_ALREADY_APPROVED")
    row.status, row.approved_by, row.approved_at, row.approval_reference = (
        "APPROVED",
        trusted.user_id,
        foundation.utcnow(),
        review_reference,
    )
    session.flush()
    foundation._audit(session, trusted, "examiner_qualification_approved", row)
    return row


def _qualification(
    session: Session,
    context: TenantContext,
    source: EvaluationReference,
    identifier: uuid.UUID,
    checksum: str,
) -> EvaluationExaminerQualification:
    _access(session, context, source)
    actual = build_tenant_context(session, context.user_id)
    # Rehydration denies inactive identities even when a stale caller presents a role.
    user = session.get(User, actual.user_id)
    if user is None or user.status.value != "ACTIVE":
        raise AuthorizationError("EVALUATION_ACTIVE_EXAMINER_REQUIRED")
    row = _locked(session, EvaluationExaminerQualification, identifier)
    _expected(row, checksum)
    if (
        row.examiner_id != context.user_id
        or row.status != "APPROVED"
        or canonical_checksum(row.body) != row.checksum
        or row.organisation_id != source.organisation_id
        or datetime.fromisoformat(row.body["expires_at"].replace("Z", "+00:00"))
        <= foundation.utcnow()
    ):
        raise AuthorizationError("EVALUATION_QUALIFIED_EXAMINER_REQUIRED")
    if (
        any(
            str(row.body.get(key)) != str(source.body.get(key))
            for key in (
                "kind",
                "language",
                "rubric_version_id",
                "rubric_checksum",
                "standard_pack_version_id",
                "standard_pack_checksum",
            )
        )
        or source.evidence_class == "APPROVED_REFERENCE"
        and row.evidence_class != "VERIFIED_CREDENTIAL"
    ):
        raise AuthorizationError("EVALUATION_EXAMINER_SCOPE_MISMATCH")
    return row


def _decision_body(
    source: EvaluationReference, payload: JudgmentInput, session: Session
) -> dict[str, Any]:
    body = payload.model_dump(mode="json")
    rubric = (
        session.get(RubricVersion, uuid.UUID(source.body["rubric_version_id"]))
        if source.body.get("rubric_version_id")
        else None
    )
    body["criteria"] = validate_judgment_evidence(
        source.body, rubric, body["criteria"], body["transcript"], body["relevance"]
    )
    return body


def submit_judgment(
    session: Session, context: TenantContext, identifier: uuid.UUID, payload: JudgmentInput
) -> EvaluationExpertJudgment:
    _operation(session, payload.operation_id)
    source = _reference(session, context, identifier)
    _expected(source, payload.expected_checksum)
    qualification = _qualification(
        session, context, source, payload.qualification_id, payload.qualification_checksum
    )
    if source.status != "APPROVED" or source.created_by == context.user_id:
        raise AuthorizationError("EVALUATION_INDEPENDENT_EXAMINER_REQUIRED")
    body = _decision_body(source, payload, session)
    checksum = canonical_checksum(body)
    old = session.scalar(
        select(EvaluationExpertJudgment).where(
            EvaluationExpertJudgment.operation_id == payload.operation_id
        )
    )
    if old:
        if (
            old.examiner_id != context.user_id
            or old.reference_id != source.id
            or old.checksum != checksum
        ):
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        return old
    if len(_judgments(session, source.id)) >= 2:
        raise foundation.EvaluationConflict("EVALUATION_REFERENCE_PROTOCOL_COMPLETE")
    row = EvaluationExpertJudgment(
        operation_id=payload.operation_id,
        reference_id=source.id,
        reference_checksum=source.checksum,
        examiner_id=context.user_id,
        qualification_id=qualification.id,
        qualification_checksum=qualification.checksum,
        body=body,
        checksum=checksum,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, context, "expert_judgment_submitted", row)
    return row


def _judgments(session: Session, identifier: uuid.UUID) -> list[EvaluationExpertJudgment]:
    rows = list(
        session.scalars(
            select(EvaluationExpertJudgment)
            .where(EvaluationExpertJudgment.reference_id == identifier)
            .order_by(EvaluationExpertJudgment.created_at, EvaluationExpertJudgment.id)
        )
    )
    if any(canonical_checksum(row.body) != row.checksum for row in rows):
        raise foundation.EvaluationConflict("EVALUATION_JUDGMENT_CHECKSUM_MISMATCH")
    return rows


def adjudicate(
    session: Session, context: TenantContext, identifier: uuid.UUID, payload: AdjudicationInput
) -> EvaluationAdjudication:
    _operation(session, payload.operation_id)
    source = _reference(session, context, identifier)
    _expected(source, payload.expected_checksum)
    qualification = _qualification(
        session, context, source, payload.qualification_id, payload.qualification_checksum
    )
    judgments = _judgments(session, source.id)
    if len(judgments) != 2 or context.user_id in {
        source.created_by,
        *(j.examiner_id for j in judgments),
    }:
        raise AuthorizationError("EVALUATION_INDEPENDENT_ADJUDICATION_REQUIRED")
    expected = {(str(j.id), j.checksum) for j in judgments}
    if {(str(pin.id), pin.checksum) for pin in payload.judgment_pins} != expected:
        raise foundation.EvaluationConflict("EVALUATION_EXACT_JUDGMENTS_REQUIRED")
    body = _decision_body(source, payload, session)
    checksum = canonical_checksum(body)
    old = session.scalar(
        select(EvaluationAdjudication).where(
            EvaluationAdjudication.operation_id == payload.operation_id
        )
    )
    if old:
        if (
            old.adjudicator_id != context.user_id
            or old.reference_id != source.id
            or old.checksum != checksum
        ):
            raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
        return old
    latest = session.scalar(
        select(EvaluationAdjudication)
        .where(EvaluationAdjudication.reference_id == source.id)
        .order_by(EvaluationAdjudication.version_number.desc())
        .limit(1)
    )
    if payload.supersedes_id != (latest.id if latest else None):
        raise foundation.EvaluationConflict("EVALUATION_ADJUDICATION_SUCCESSOR_CONFLICT")
    row = EvaluationAdjudication(
        operation_id=payload.operation_id,
        reference_id=source.id,
        reference_checksum=source.checksum,
        version_number=(latest.version_number + 1 if latest else 1),
        supersedes_id=payload.supersedes_id,
        adjudicator_id=context.user_id,
        qualification_id=qualification.id,
        body=body,
        checksum=checksum,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, context, "expert_adjudicated", row)
    return row


def reference_summary(session: Session, row: EvaluationReference) -> dict[str, Any]:
    count = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationExpertJudgment)
            .where(EvaluationExpertJudgment.reference_id == row.id)
        )
        or 0
    )
    adjudications = (
        session.scalar(
            select(func.count())
            .select_from(EvaluationAdjudication)
            .where(EvaluationAdjudication.reference_id == row.id)
        )
        or 0
    )
    versions = list(
        session.scalars(
            select(EvaluationDatasetVersion).where(
                EvaluationDatasetVersion.dataset_id == row.dataset_id
            )
        )
    )
    return {
        "id": str(row.id),
        "dataset_id": str(row.dataset_id),
        "checksum": row.checksum,
        "kind": row.kind,
        "split": row.split,
        "evidence_class": row.evidence_class,
        "status": row.status,
        "judgment_count": count,
        "required_judgment_count": 2,
        "adjudication_count": adjudications,
        "adjudication_status": "ADJUDICATED" if adjudications else "PENDING",
        "dataset_version_ids": [
            str(v.id) for v in versions if v.provenance.get("reference_id") == str(row.id)
        ],
        "empirical_thresholds": "UNCONFIGURED",
        "limitations": LIMITATIONS,
        "production_activation": False,
    }


def workbench(session: Session, context: TenantContext, identifier: uuid.UUID) -> dict[str, Any]:
    source = _reference(session, context, identifier, lock=False)
    trusted = build_tenant_context(session, context.user_id)
    qualifications = []
    for q in session.scalars(
        select(EvaluationExaminerQualification).where(
            EvaluationExaminerQualification.examiner_id == context.user_id,
            EvaluationExaminerQualification.status == "APPROVED",
        )
    ):
        try:
            _qualification(session, context, source, q.id, q.checksum)
            qualifications.append({"id": str(q.id), "checksum": q.checksum})
        except (AuthorizationError, ValueError):
            continue
    manager = {foundation.MANAGE, foundation.REVIEW} <= trusted.platform_permissions
    if not qualifications and not manager:
        raise AuthorizationError("EVALUATION_PRIVATE_REFERENCE_ACCESS_REQUIRED")
    all_judgments = _judgments(session, source.id)
    independent = bool(qualifications) and context.user_id not in {
        source.created_by,
        *(j.examiner_id for j in all_judgments),
    }
    visible = (
        all_judgments
        if independent and len(all_judgments) == 2
        else [j for j in all_judgments if j.examiner_id == context.user_id]
    )
    actions = []
    if manager and source.status == "PENDING_APPROVAL" and source.created_by != context.user_id:
        actions.append("APPROVE_SOURCE")
    if (
        qualifications
        and source.status == "APPROVED"
        and source.created_by != context.user_id
        and len(all_judgments) < 2
        and not any(j.examiner_id == context.user_id for j in all_judgments)
    ):
        actions.append("SUBMIT_JUDGMENT")
    if independent and len(all_judgments) == 2:
        actions.append("ADJUDICATE")
    adjudications = list(
        session.scalars(
            select(EvaluationAdjudication)
            .where(EvaluationAdjudication.reference_id == source.id)
            .order_by(EvaluationAdjudication.version_number)
        )
    )
    if manager and adjudications:
        actions.append("DERIVE_DATASET")
    rubric = (
        session.get(RubricVersion, uuid.UUID(source.body["rubric_version_id"]))
        if source.body.get("rubric_version_id")
        else None
    )
    return {
        "reference": reference_summary(session, source),
        "source": source.body,
        "rubric": {"criteria": rubric.criteria, "performance_levels": rubric.performance_levels}
        if rubric
        else None,
        "qualifications": qualifications,
        "judgments": [
            {
                "id": str(j.id),
                "checksum": j.checksum,
                "examiner_id": str(j.examiner_id),
                "body": j.body,
                "created_at": j.created_at.isoformat(),
            }
            for j in visible
        ],
        "adjudications": [
            {
                "id": str(a.id),
                "checksum": a.checksum,
                "version_number": a.version_number,
                "body": a.body,
            }
            for a in adjudications
            if independent or a.adjudicator_id == context.user_id
        ],
        "allowed_actions": actions,
    }


def derive_dataset(
    session: Session, context: TenantContext, identifier: uuid.UUID, payload: ReferenceDatasetInput
) -> EvaluationDatasetVersion:
    trusted = foundation.require(session, context, foundation.MANAGE, foundation.RUN)
    source = _reference(session, context, identifier)
    _expected(source, payload.expected_checksum)
    adjudication = session.get(EvaluationAdjudication, payload.adjudication_id)
    if (
        not adjudication
        or adjudication.reference_id != source.id
        or adjudication.checksum != payload.adjudication_checksum
        or canonical_checksum(adjudication.body) != adjudication.checksum
    ):
        raise foundation.EvaluationConflict("EVALUATION_EXACT_ADJUDICATION_REQUIRED")
    judgments = _judgments(session, source.id)
    if len(judgments) != 2:
        raise foundation.EvaluationConflict("EVALUATION_INDEPENDENT_JUDGMENTS_REQUIRED")
    _operation(session, payload.operation_id)
    request_checksum = canonical_checksum(payload.model_dump(mode="json"))
    for previous in session.scalars(select(EvaluationDatasetVersion)):
        if previous.provenance.get("operation_id") == str(payload.operation_id):
            if (
                previous.created_by != context.user_id
                or previous.dataset_id != source.dataset_id
                or previous.provenance.get("request_checksum") != request_checksum
            ):
                raise foundation.EvaluationConflict("EVALUATION_OPERATION_CONFLICT")
            return previous
    reference = {
        "label_kind": "SYNTHETIC_EXPECTATION"
        if source.evidence_class == "SYNTHETIC_TEST"
        else "ADJUDICATED_EXPERT",
        "language": source.body["language"],
        "evidence_identity": copy.deepcopy(source.body["input"]["evidence_identity"]),
        "criteria": [],
        "text": adjudication.body.get("transcript"),
    }
    rubric = (
        session.get(RubricVersion, uuid.UUID(source.body["rubric_version_id"]))
        if source.body.get("rubric_version_id")
        else None
    )
    if rubric:
        by_code = {c["criterion_code"]: c for c in adjudication.body["criteria"]}
        reference["criteria"] = [
            {
                "code": c["code"],
                "points": by_code[c["code"]]["points"],
                "max_points": str(c["max_points"]),
                "judgments": []
                if source.evidence_class == "SYNTHETIC_TEST"
                else [
                    {
                        "reviewer_id": str(j.examiner_id),
                        "points": next(
                            v["points"]
                            for v in j.body["criteria"]
                            if v["criterion_code"] == c["code"]
                        ),
                    }
                    for j in judgments
                ],
            }
            for c in rubric.criteria
        ]
    if payload.execution_job_id:
        models = {
            "WRITING": WritingExecutionJob,
            "SPEAKING": SpeakingExecutionJob,
            "TRANSCRIPTION": SpeechTranscriptionJob,
        }
        job_model = models.get(source.kind)
        job: Any = session.get(job_model, payload.execution_job_id) if job_model else None
        identity = source.body["input"]["evidence_identity"]
        if (
            job is None
            or job.organisation_id != source.organisation_id
            or any(str(job.source_identity.get(key)) != value for key, value in identity.items())
        ):
            raise foundation.EvaluationConflict("EVALUATION_EXACT_EXECUTION_SOURCE_REQUIRED")
    case = {
        "case_id": "reference-" + str(source.id),
        "kind": source.kind,
        "split": source.split,
        "input": source.body["input"],
        "reference": reference,
        "synthetic_observation": None,
        "execution_job_id": str(payload.execution_job_id) if payload.execution_job_id else None,
    }
    if source.kind == "RETRIEVAL":
        reference["sources"] = [
            {
                "id": value["document_version_id"],
                "version_id": value["document_version_id"],
                "checksum": value["checksum"],
                "organisation_id": str(source.organisation_id) if source.organisation_id else None,
                "authorized": True,
                "relevant": value["relevant"],
            }
            for value in adjudication.body["relevance"]
        ]
    provenance = {
        "origin": "GOVERNED_REFERENCE",
        "request_checksum": request_checksum,
        "operation_id": str(payload.operation_id),
        "reference_id": str(source.id),
        "reference_checksum": source.checksum,
        "adjudication_id": str(adjudication.id),
        "adjudication_checksum": adjudication.checksum,
        "execution_job_id": str(payload.execution_job_id) if payload.execution_job_id else None,
        "judgment_pins": [{"id": str(j.id), "checksum": j.checksum} for j in judgments],
        "evidence_class": "SYNTHETIC_TEST"
        if source.evidence_class == "SYNTHETIC_TEST"
        else "EXPERT_LABELLED",
        "source_scope": {
            k: source.body.get(k)
            for k in (
                "standard_pack_version_id",
                "standard_pack_checksum",
                "rubric_version_id",
                "rubric_checksum",
                "question_bank_item_version_id",
                "item_checksum",
            )
        },
        "split": source.split,
        "purpose": "EVALUATION_ONLY",
        "empirical_thresholds": "UNCONFIGURED",
    }
    checksum = canonical_checksum({"provenance": provenance, "cases": [case]})
    version = (
        session.scalar(
            select(func.max(EvaluationDatasetVersion.version_number)).where(
                EvaluationDatasetVersion.dataset_id == source.dataset_id
            )
        )
        or 0
    ) + 1
    row = EvaluationDatasetVersion(
        dataset_id=source.dataset_id,
        version_number=version,
        status="DRAFT",
        assessment_family=source.body["assessment_family"],
        language=source.body["language"],
        cefr_level=source.body.get("cefr_level"),
        skill=source.kind,
        provenance=provenance,
        cases=[case],
        checksum=checksum,
        case_count=1,
        evidence_class="SYNTHETIC_TEST"
        if source.evidence_class == "SYNTHETIC_TEST"
        else "EXPERT_LABELLED",
        created_by=trusted.user_id,
        created_at=foundation.utcnow(),
    )
    session.add(row)
    session.flush()
    foundation._audit(session, trusted, "expert_dataset_derived", row)
    return row


def validate_dataset_transition(
    session: Session, context: TenantContext, dataset: EvaluationDatasetVersion, action: str
) -> None:
    """Publication cannot turn an adjudicator's own outcome into self-approval."""
    provenance = dataset.provenance
    source = _reference(session, context, uuid.UUID(provenance["reference_id"]))
    _expected(source, provenance["reference_checksum"])
    adjudication = session.get(EvaluationAdjudication, uuid.UUID(provenance["adjudication_id"]))
    judgments = _judgments(session, source.id)
    if (
        source.status != "APPROVED"
        or adjudication is None
        or adjudication.reference_id != source.id
        or adjudication.checksum != provenance["adjudication_checksum"]
        or canonical_checksum(adjudication.body) != adjudication.checksum
        or {(str(j.id), j.checksum) for j in judgments}
        != {(pin["id"], pin["checksum"]) for pin in provenance["judgment_pins"]}
        or len(judgments) != 2
    ):
        raise foundation.EvaluationConflict("EVALUATION_REFERENCE_PUBLICATION_PIN_MISMATCH")
    for judgment in judgments:
        _qualification(
            session,
            build_tenant_context(session, judgment.examiner_id),
            source,
            judgment.qualification_id,
            judgment.qualification_checksum,
        )
    _qualification(
        session,
        build_tenant_context(session, adjudication.adjudicator_id),
        source,
        adjudication.qualification_id,
        adjudication.body["qualification_checksum"],
    )
    if action == "approve" and context.user_id in {
        source.created_by,
        adjudication.adjudicator_id,
        *(j.examiner_id for j in judgments),
    }:
        raise AuthorizationError("EVALUATION_INDEPENDENT_REFERENCE_APPROVAL_REQUIRED")
