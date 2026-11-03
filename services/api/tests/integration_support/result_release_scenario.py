"""Small real-service release scenario, extending the canonical Step 1 graph.

Historical correction evidence is never rewritten to manufacture an eligible
release. A new exam version and candidate attempt take the normal runtime,
scoring and human approval path. These records are synthetic test material.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.exams.service import ExamStudioService
from app.models import (
    AssessmentResult,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateResponse,
    CandidateResponseRevision,
    ExamVersion,
    ExamVersionItemBinding,
    ExamVersionModule,
    ExamVersionSection,
    ExamVersionTask,
    QuestionBankItemVersion,
    ResultVersion,
    ScoringRun,
)
from app.models.enums import (
    AssessmentClassification,
    CandidateAssignmentStatus,
    CandidateAttemptStatus,
    ExamOriginType,
    ExamVersionStatus,
    QuestionBankLifecycleStatus,
    ResultVersionStatus,
)
from app.runtime.service import CandidateRuntimeService
from app.scoring.service import ScoringService, checksum
from tests.integration_support.result_correction_scenario import (
    GovernedResultCorrectionScenario,
    build_governed_result_correction_scenario,
)


@dataclass(frozen=True)
class GovernedReleaseScenario:
    historical: GovernedResultCorrectionScenario
    context: TenantContext
    candidate_context: TenantContext
    exam_version: ExamVersion
    item_version: QuestionBankItemVersion
    binding: ExamVersionItemBinding
    attempt: CandidateExamAttempt
    result: AssessmentResult
    run: ScoringRun
    version: ResultVersion


def release_context(
    organisation_id: uuid.UUID, user_id: uuid.UUID, *, permissions: set[str] | None = None
) -> TenantContext:
    return TenantContext(
        user_id=user_id,
        organisation_id=organisation_id,
        membership_id=None,
        permissions=frozenset(
            permissions
            if permissions is not None
            else {
                "exam_assignment.manage",
                "scoring.run",
                "result.review",
                "result.release",
                "result.manage",
                "result.read",
                "appeal.adjudicate",
                "appeal.correct",
            }
        ),
    )


def build_governed_release_scenario(
    session: Session,
    *,
    classification: AssessmentClassification = AssessmentClassification.PRACTICE,
    provider_hint: str = "SYNTHETIC",
    historical: GovernedResultCorrectionScenario | None = None,
) -> GovernedReleaseScenario:
    historical = historical or build_governed_result_correction_scenario(session)
    now = datetime.now(timezone.utc)
    label = f"release-runtime-{uuid.uuid4().hex[:12]}"
    context = release_context(historical.organisation.id, historical.reviewer.id)
    candidate_context = release_context(
        historical.organisation.id, historical.second_candidate.id, permissions=set()
    )
    source = historical.objective_item_version_i1
    content = {
        "prompt": f"SYNTHETIC {provider_hint} B1 release test: select option A.",
        "options": [
            {"option_id": "a", "text": "Synthetic option A"},
            {"option_id": "b", "text": "Synthetic option B"},
        ],
        "correct_option_ids": ["a"],
    }
    item = QuestionBankItemVersion(
        item_id=source.item_id,
        version_label=label,
        status=QuestionBankLifecycleStatus.ACTIVE,
        assessment_family=source.assessment_family,
        language=source.language,
        skill=source.skill,
        task_type=source.task_type,
        response_mode=source.response_mode,
        media_requirement=source.media_requirement,
        content=content,
        scoring_policy=source.scoring_policy,
        max_points=Decimal("1"),
        scoring_metadata=source.scoring_metadata,
        rights_status=source.rights_status,
        origin_type=source.origin_type,
        created_at=now,
        checksum=checksum(content),
    )
    exam = ExamVersion(
        exam_id=historical.exam.id,
        version_label=label,
        status=ExamVersionStatus.PUBLISHED,
        standard_pack_version_id=historical.standard_pack_version.id,
        classification=classification,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
        checksum=checksum("synthetic release exam"),
    )
    session.add_all([item, exam])
    session.flush()
    module = ExamVersionModule(
        exam_version_id=exam.id,
        standard_pack_module_id=historical.standard_pack_module.id,
        ordinal=1,
    )
    session.add(module)
    session.flush()
    section = ExamVersionSection(
        exam_version_module_id=module.id,
        standard_pack_section_id=historical.standard_pack_section.id,
        ordinal=1,
    )
    session.add(section)
    session.flush()
    task = ExamVersionTask(
        exam_version_section_id=section.id,
        standard_pack_task_spec_id=historical.standard_pack_task.id,
        ordinal=1,
    )
    session.add(task)
    session.flush()
    binding = ExamVersionItemBinding(
        exam_version_task_id=task.id,
        slot_index=0,
        question_bank_item_version_id=item.id,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
    )
    session.add(binding)
    session.flush()
    ExamStudioService._refresh_checksum(session, exam)
    if classification is AssessmentClassification.OFFICIAL_CERTIFICATION:
        # Only a negative release fixture: official runtime delivery stays
        # blocked. This new submitted prerequisite has never been released.
        plan = {
            "schema_version": "1",
            "exam_version_id": str(exam.id),
            "standard_pack_version_id": str(historical.standard_pack_version.id),
            "classification": classification.value,
            "modules": [
                {
                    "module_id": str(module.id),
                    "sections": [
                        {
                            "section_id": str(section.id),
                            "tasks": [
                                {
                                    "task_id": str(task.id),
                                    "items": [
                                        {
                                            "binding_id": str(binding.id),
                                            "item_version_id": str(item.id),
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        assignment = CandidateExamAssignment(
            organisation_id=historical.organisation.id,
            candidate_user_id=historical.second_candidate.id,
            exam_version_id=exam.id,
            status=CandidateAssignmentStatus.COMPLETED,
            maximum_attempts=1,
            created_at=now,
        )
        session.add(assignment)
        session.flush()
        attempt = CandidateExamAttempt(
            assignment_id=assignment.id,
            candidate_user_id=historical.second_candidate.id,
            organisation_id=historical.organisation.id,
            exam_version_id=exam.id,
            attempt_number=1,
            status=CandidateAttemptStatus.SUBMITTED,
            delivery_manifest_checksum=checksum(plan),
            runtime_plan_checksum=checksum(plan),
            runtime_plan=plan,
            submitted_at=now,
            created_at=now,
        )
        session.add(attempt)
        session.flush()
        response = CandidateResponse(
            attempt=attempt,
            exam_version_item_binding_id=binding.id,
            current_revision=1,
            current_payload={"option_id": "a"},
            finalized_at=now,
        )
        session.add(response)
        session.flush()
        session.add(
            CandidateResponseRevision(
                candidate_response_id=response.id,
                attempt_id=attempt.id,
                revision_number=1,
                client_operation_id=uuid.uuid4(),
                payload={"option_id": "a"},
                payload_checksum=checksum({"option_id": "a"}),
                received_at=now,
            )
        )
        session.flush()
    else:
        assignment = CandidateRuntimeService.create_assignment(
            session,
            context,
            candidate_user_id=historical.second_candidate.id,
            exam_version_id=exam.id,
        )
        attempt = CandidateRuntimeService.create_attempt(session, candidate_context, assignment.id)
        CandidateRuntimeService.start(session, candidate_context, attempt.id)
        CandidateRuntimeService.save_response(
            session,
            candidate_context,
            attempt.id,
            binding.id,
            payload={"option_id": "a"},
            client_operation_id=uuid.uuid4(),
            expected_revision=0,
        )
        CandidateRuntimeService.submit(session, candidate_context, attempt.id)
    run = ScoringService.score(session, context, attempt.id)
    version = session.scalar(select(ResultVersion).where(ResultVersion.scoring_run_id == run.id))
    assert version is not None
    assert version.raw_awarded_points == Decimal("1")
    assert version.raw_maximum_points == Decimal("1")
    assert version.status is ResultVersionStatus.REVIEW_REQUIRED
    ScoringService.request_review(session, context, version.id)
    approved = ScoringService.approve_review(session, context, version.id)
    result = session.get(AssessmentResult, version.result_id)
    assert result is not None
    assert approved.status is ResultVersionStatus.APPROVED
    return GovernedReleaseScenario(
        historical,
        context,
        candidate_context,
        exam,
        item,
        binding,
        attempt,
        result,
        run,
        version,
    )
