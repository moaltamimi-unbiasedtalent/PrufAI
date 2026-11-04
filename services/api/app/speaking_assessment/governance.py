"""Human-only Speaking on the shared immutable subjective assessment workflow."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.authorization.membership import (
    require_active_tenant_permission as require_tenant_membership,
)
from app.models import (
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    QuestionBankItemVersion,
    RubricVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
)
from app.models.enums import (
    CandidateAttemptStatus,
    HumanReviewStatus,
    QuestionBankSkill,
    SubjectiveAssessmentStatus,
    SubjectiveCriterionStatus,
)
from app.speaking_audio.service import SpeakingAudioService
from app.writing_assessment.governance import (
    HumanWritingService,
    _permission,
    _receipt,
    _record,
)
from app.writing_assessment.service import (
    RubricError,
    SubjectiveAssessmentService,
    _decimal,
    canonical_checksum,
)


class HumanSpeakingService(HumanWritingService):
    SKILL = QuestionBankSkill.SPEAKING
    REVIEW_TYPE = "HUMAN_SPEAKING_ASSESSMENT"

    @classmethod
    def _version(
        cls, session: Session, context: TenantContext, version_id: uuid.UUID, permission: str
    ) -> SubjectiveAssessmentVersion:
        require_tenant_membership(session, context, permission)
        candidate = session.get(SubjectiveAssessmentVersion, version_id)
        if candidate is None or candidate.assessment.organisation_id != context.organisation_id:
            raise RubricError("human assessment is not available in this organisation")
        session.scalar(
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == candidate.attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return super()._version(session, context, version_id, permission)

    @staticmethod
    def assert_human_mode(rubric: RubricVersion) -> None:
        HumanWritingService.assert_human_mode(rubric)
        if (
            rubric.skill is not QuestionBankSkill.SPEAKING
            or rubric.evidence_policy.get("mode") != "AUDIO"
            or rubric.evidence_policy.get("time_unit") != "milliseconds"
            or rubric.evidence_policy.get("required") is not True
            or type(rubric.evidence_policy.get("minimum_references")) is not int
            or not 1 <= rubric.evidence_policy["minimum_references"] <= 20
            or any(criterion.get("evidence_mode") != "AUDIO" for criterion in rubric.criteria)
            or rubric.human_review_policy.get("required") is not True
            or rubric.human_review_policy.get("separation_of_duties") is not True
        ):
            raise RubricError(
                "human Speaking requires explicit AUDIO evidence and independent review"
            )

    @staticmethod
    def _audio(
        session: Session,
        attempt: CandidateExamAttempt,
        revision: CandidateResponseRevision,
        binding_id: uuid.UUID,
    ) -> CandidateMediaAsset:
        if attempt.status is not CandidateAttemptStatus.SUBMITTED:
            raise RubricError("human Speaking requires a submitted attempt")
        if (
            revision.attempt_id != attempt.id
            or revision.response.attempt_id != attempt.id
            or revision.response.exam_version_item_binding_id != binding_id
            or revision.response.current_revision != revision.revision_number
        ):
            raise RubricError(
                "Speaking evidence is not the authoritative submitted response revision"
            )
        bound = session.scalar(
            select(CandidateResponseMediaBinding).where(
                CandidateResponseMediaBinding.candidate_response_revision_id == revision.id,
                CandidateResponseMediaBinding.binding_role == "PRIMARY_AUDIO",
            )
        )
        asset = session.get(CandidateMediaAsset, bound.candidate_media_asset_id) if bound else None
        if asset is None or revision.payload != {"media_asset_id": str(asset.id)}:
            raise RubricError("Speaking requires the exact attached primary audio response")
        try:
            SpeakingAudioService.validate_asset_identity(session, attempt, binding_id, asset)
            SpeakingAudioService._require_complete_evidence_validation(session, asset)
        except ValueError as exc:
            raise RubricError(str(exc)) from exc
        recording = asset.recording_session
        proof = asset.validation_provenance
        if (
            asset.duration_ms is None
            or asset.duration_ms <= 0
            or not asset.sha256
            or len(asset.sha256) != 64
            or recording.validation_provenance != proof
            or proof.get("validator") != "ffprobe_and_complete_ffmpeg_audio_decode"
            or proof.get("source_sha256") != asset.sha256
            or proof.get("source_byte_size") != asset.byte_size
            or proof.get("duration_ms") != asset.duration_ms
            or proof.get("duration_source") != "decoded_pcm_frame_samples"
            or not isinstance(proof.get("original_presentation_span_ms"), (int, float))
            or proof["original_presentation_span_ms"] <= 0
        ):
            raise RubricError("Speaking audio lacks complete original-byte and duration provenance")
        return asset

    @classmethod
    def _pins(
        cls, session: Session, version: SubjectiveAssessmentVersion
    ) -> tuple[RubricVersion, CandidateResponseRevision]:
        rubric, revision = super()._pins(session, version)
        attempt = session.get(CandidateExamAttempt, version.attempt_id)
        if (
            attempt is None
            or attempt.organisation_id != version.assessment.organisation_id
            or attempt.candidate_user_id != version.assessment.candidate_user_id
            or attempt.exam_version_id != version.exam_version_id
            or str(rubric.standard_pack_version_id)
            != str(attempt.runtime_plan.get("standard_pack_version_id"))
            or version.standard_pack_version_id != rubric.standard_pack_version_id
        ):
            raise RubricError("Speaking assessment ownership or StandardPackVersion is stale")
        asset = cls._audio(session, attempt, revision, version.exam_version_item_binding_id)
        if (
            version.candidate_media_asset_id != asset.id
            or version.audio_sha256 != asset.sha256
            or version.speech_transcription_version_id is not None
            or version.transcript_checksum is not None
            or version.evidence_mode_summary
            != {str(item["code"]): "AUDIO" for item in rubric.criteria}
        ):
            raise RubricError("human Speaking evidence does not match its exact audio lineage")
        pins = version.provenance.get("audio_evidence")
        if pins is not None and pins != cls.audio_pins(asset, revision):
            raise RubricError("human Speaking audio provenance changed")
        return rubric, revision

    @staticmethod
    def audio_pins(
        asset: CandidateMediaAsset, revision: CandidateResponseRevision
    ) -> dict[str, Any]:
        return {
            "candidate_media_asset_id": str(asset.id),
            "audio_sha256": asset.sha256,
            "candidate_response_revision_id": str(revision.id),
            "recording_session_id": str(asset.recording_session_id),
            "duration_ms": asset.duration_ms,
            "byte_size": asset.byte_size,
            "policy_checksum": asset.recording_session.evidence_identity["policy_checksum"],
            "item_version_checksum": asset.recording_session.evidence_identity[
                "item_version_checksum"
            ],
            "validation_checksum": canonical_checksum(asset.validation_provenance),
            "time_unit": "milliseconds",
            "time_origin": "original_audio_playback_start",
            "span_bound": "verified_decoded_duration_ms",
        }

    @classmethod
    def initialize(
        cls,
        session: Session,
        context: TenantContext,
        *,
        operation_id: uuid.UUID,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        response_revision_id: uuid.UUID | None = None,
        rubric_version_id: uuid.UUID,
        response_checksum: str,
        rubric_checksum: str,
        _source: SubjectiveAssessmentVersion | None = None,
        _reason: str | None = None,
        _result_correction_id: uuid.UUID | None = None,
    ) -> SubjectiveAssessmentVersion:
        _permission(context, "subjective.assess")
        require_tenant_membership(session, context, "subjective.assess")
        attempt = session.scalar(
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.organisation_id != context.organisation_id:
            raise RubricError("attempt is not available in this organisation")
        if attempt.candidate_user_id == context.user_id:
            raise PermissionError("candidate cannot assess their own Speaking response")
        action = (
            "subjective_assessment.human_initialized"
            if _source is None
            else ("subjective_assessment.human_reassessment_initialized")
        )
        target_id = attempt.id if _source is None else _source.id
        payload = {
            "binding_id": str(binding_id),
            "response_revision_id": str(response_revision_id),
            "rubric_version_id": str(rubric_version_id),
            "response_checksum": response_checksum,
            "rubric_checksum": rubric_checksum,
            "source_checksum": _source.checksum if _source else None,
            "reason": _reason,
        }
        correction_pins = None
        if _result_correction_id is not None:
            if _source is None:
                raise RubricError("Speaking correction requires an exact source version")
            from app.result_governance.service import ResultGovernanceService

            try:
                correction_pins = ResultGovernanceService.human_speaking_correction_pins(
                    session, _source, _result_correction_id, phase="initializing"
                )
            except ValueError as exc:
                raise RubricError(str(exc)) from exc
            payload["result_correction_id"] = str(_result_correction_id)
        receipt = _receipt(session, context, operation_id, action, target_id, payload)
        if receipt:
            result = session.get(SubjectiveAssessmentVersion, receipt.result_id)
            if result is None:
                raise RubricError("governance replay result is unavailable")
            return result
        existing = session.scalar(
            select(SubjectiveAssessment).where(
                SubjectiveAssessment.attempt_id == attempt.id,
                SubjectiveAssessment.exam_version_item_binding_id == binding_id,
            )
        )
        latest = (
            max(existing.versions, key=lambda row: row.version_number, default=None)
            if existing
            else None
        )
        if _source is not None and (latest is None or latest.id != _source.id):
            raise RubricError("Speaking reassessment requires the latest exact source version")
        if _source is None and latest is not None:
            raise RubricError("Speaking history requires an explicit governed successor")
        authoritative = (
            session.scalars(
                select(SubjectiveAssessmentVersion)
                .where(
                    SubjectiveAssessmentVersion.subjective_assessment_id == existing.id,
                    SubjectiveAssessmentVersion.status == SubjectiveAssessmentStatus.APPROVED,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            if existing
            else []
        )
        if len(authoritative) > 1:
            raise RubricError("Speaking history has more than one authoritative approval")
        approved_source = authoritative[0] if authoritative else None
        response = session.scalar(
            select(CandidateResponse).where(
                CandidateResponse.attempt_id == attempt.id,
                CandidateResponse.exam_version_item_binding_id == binding_id,
            )
        )
        revision = (
            max(response.revisions, key=lambda row: row.revision_number, default=None)
            if response
            else None
        )
        if revision is None or revision.id != response_revision_id:
            raise RubricError(
                "Speaking initialization requires the exact submitted response revision"
            )
        asset = cls._audio(session, attempt, revision, binding_id)
        version = SubjectiveAssessmentService.create(
            session,
            context,
            attempt_id=attempt.id,
            binding_id=binding_id,
            rubric_version_id=rubric_version_id,
            skill=QuestionBankSkill.SPEAKING,
            media_asset_id=asset.id,
            _human_origin=True,
            _human_successor_of=_source.id if _source else None,
        )
        version.assessment_origin = "HUMAN"
        rubric, _revision = cls._pins(session, version)
        if (
            version.response_checksum != response_checksum
            or version.rubric_checksum != rubric_checksum
        ):
            raise RubricError("stale Speaking response or rubric checksum")
        version.status = SubjectiveAssessmentStatus.REVIEW_REQUIRED
        version.provenance = {
            "assessment_origin": "HUMAN",
            "skill": "SPEAKING",
            "evidence_mode": "AUDIO",
            "audio_evidence": cls.audio_pins(asset, revision),
            "initialized_by": str(context.user_id),
            "initialized_at": datetime.now(timezone.utc).isoformat(),
            **(
                {
                    "result_correction_id": str(_result_correction_id),
                    "result_correction": correction_pins,
                }
                if correction_pins is not None
                else {}
            ),
            **(
                {
                    "source_version_id": str(_source.id),
                    "source_checksum": _source.checksum,
                    "reassessment_reason": _reason,
                    "authoritative_source_version_id": str(approved_source.id)
                    if approved_source
                    else None,
                    "authoritative_source_checksum": approved_source.checksum
                    if approved_source
                    else None,
                }
                if _source
                else {}
            ),
        }
        version.checksum = canonical_checksum(
            {
                "origin": "HUMAN",
                "response_revision_id": str(revision.id),
                "response_checksum": response_checksum,
                "rubric_checksum": rubric_checksum,
                "task_spec_id": str(rubric.task_spec_id),
                "provenance": version.provenance,
            }
        )
        _record(
            session,
            context,
            operation_id,
            action,
            target_id,
            payload,
            version.id,
            entity_type="SubjectiveAssessmentVersion",
            checksum=version.checksum,
            source_version_id=_source.id if _source else None,
        )
        session.flush()
        return version

    @classmethod
    def _entries(
        cls,
        session: Session,
        version: SubjectiveAssessmentVersion,
        rubric: RubricVersion,
        revision: CandidateResponseRevision,
        criteria: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
        asset = session.get(CandidateMediaAsset, version.candidate_media_asset_id)
        if asset is None or asset.duration_ms is None:
            raise RubricError("verified Speaking duration is unavailable")
        return cls.validate_audio_entries(rubric, asset, revision.id, criteria)

    @staticmethod
    def validate_audio_entries(
        rubric: RubricVersion,
        asset: CandidateMediaAsset,
        revision_id: uuid.UUID,
        entries: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
        expected = SubjectiveAssessmentService._criterion_map(rubric)
        if (
            not isinstance(entries, list)
            or len(entries) != len(expected)
            or len(entries) > 50
            or any(not isinstance(entry, dict) for entry in entries)
            or {str(entry.get("criterion_code")) for entry in entries} != set(expected)
        ):
            raise RubricError("human proposal must cover every rubric criterion exactly once")
        levels = {str(level["code"]) for level in rubric.performance_levels}
        normalized, values = [], {}
        for entry in entries:
            if set(entry) != {"criterion_code", "points", "level", "rationale", "evidence"}:
                raise RubricError("human Speaking criterion contains unsupported or AI fields")
            code, level = str(entry["criterion_code"]), str(entry["level"])
            points = _decimal(entry["points"])
            rationale, evidence = entry["rationale"], entry["evidence"]
            if points < 0 or points > _decimal(expected[code]["max_points"]) or level not in levels:
                raise RubricError("human criterion score or level exceeds rubric bounds")
            if points != points.quantize(Decimal("0.0001")):
                raise RubricError("human criterion points support at most four decimal places")
            if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2000:
                raise RubricError("human criterion rationale is required and bounded")
            if (
                not isinstance(evidence, list)
                or not rubric.evidence_policy["minimum_references"] <= len(evidence) <= 20
            ):
                raise RubricError("human Speaking requires bounded audio evidence references")
            for reference in evidence:
                if not isinstance(reference, dict) or set(reference) != {
                    "asset_id",
                    "audio_sha256",
                    "candidate_response_revision_id",
                    "start_ms",
                    "end_ms",
                    "time_unit",
                }:
                    raise RubricError("audio evidence requires exact lineage and millisecond spans")
                start, end = reference["start_ms"], reference["end_ms"]
                if (
                    reference["asset_id"] != str(asset.id)
                    or reference["audio_sha256"] != asset.sha256
                    or reference["candidate_response_revision_id"] != str(revision_id)
                    or reference["time_unit"] != "milliseconds"
                    or type(start) is not int
                    or type(end) is not int
                    or asset.duration_ms is None
                    or not 0 <= start < end <= asset.duration_ms
                ):
                    raise RubricError(
                        "audio evidence does not match verified audio identity or duration"
                    )
            normalized.append(
                {
                    "criterion_code": code,
                    "points": format(points, ".4f"),
                    "level": level,
                    "rationale": rationale.strip(),
                    "evidence": evidence,
                }
            )
            values[code] = points
        return sorted(normalized, key=lambda item: item["criterion_code"]), values

    @classmethod
    def successor(
        cls,
        session: Session,
        context: TenantContext,
        version_id: uuid.UUID,
        *,
        operation_id: uuid.UUID,
        expected_checksum: str,
        reason: str,
        _result_correction_id: uuid.UUID | None = None,
    ) -> SubjectiveAssessmentVersion:
        # Use the initialization lock order before taking the source-version lock.
        # This also serializes retries against a newer terminal descendant.
        candidate = session.get(SubjectiveAssessmentVersion, version_id)
        require_tenant_membership(session, context, "subjective.assess")
        if candidate is None or candidate.assessment.organisation_id != context.organisation_id:
            raise RubricError("human assessment is not available in this organisation")
        session.scalar(
            select(CandidateExamAttempt)
            .where(
                CandidateExamAttempt.id == candidate.attempt_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        source = cls._version(session, context, version_id, "subjective.assess")
        if (
            source.status
            not in {
                SubjectiveAssessmentStatus.APPROVED,
                SubjectiveAssessmentStatus.SUPERSEDED,
                SubjectiveAssessmentStatus.REJECTED,
                SubjectiveAssessmentStatus.CHANGES_REQUESTED,
            }
            or source.checksum != expected_checksum
        ):
            raise RubricError("Speaking reassessment requires the exact approved source checksum")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise RubricError("Speaking reassessment requires a bounded reason")
        cls._pins(session, source)
        return cls.initialize(
            session,
            context,
            operation_id=operation_id,
            attempt_id=source.attempt_id,
            binding_id=source.exam_version_item_binding_id,
            response_revision_id=source.candidate_response_revision_id,
            rubric_version_id=source.rubric_version_id,
            response_checksum=source.response_checksum,
            rubric_checksum=source.rubric_checksum,
            _source=source,
            _reason=reason.strip(),
            _result_correction_id=_result_correction_id,
        )

    @staticmethod
    def _after_decision(
        session: Session, version: SubjectiveAssessmentVersion, decision: HumanReviewStatus
    ) -> None:
        if decision is not HumanReviewStatus.APPROVED or not version.provenance.get(
            "source_version_id"
        ):
            return
        source = session.scalar(
            select(SubjectiveAssessmentVersion)
            .where(
                SubjectiveAssessmentVersion.id == uuid.UUID(version.provenance["source_version_id"])
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            source is None
            or source.status
            not in {
                SubjectiveAssessmentStatus.APPROVED,
                SubjectiveAssessmentStatus.REJECTED,
                SubjectiveAssessmentStatus.CHANGES_REQUESTED,
            }
            or source.checksum != version.provenance.get("source_checksum")
            or source.id != version.supersedes_version_id
            or source.subjective_assessment_id != version.subjective_assessment_id
        ):
            raise RubricError(
                "Speaking successor no longer matches its authoritative approved source"
            )
        authoritative = session.scalars(
            select(SubjectiveAssessmentVersion)
            .where(
                SubjectiveAssessmentVersion.subjective_assessment_id
                == version.subjective_assessment_id,
                SubjectiveAssessmentVersion.id != version.id,
                SubjectiveAssessmentVersion.status == SubjectiveAssessmentStatus.APPROVED,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        expected_id = version.provenance.get("authoritative_source_version_id")
        expected_checksum = version.provenance.get("authoritative_source_checksum")
        if (
            len(authoritative) > 1
            or (expected_id is None and authoritative)
            or (
                expected_id is not None
                and (
                    len(authoritative) != 1
                    or str(authoritative[0].id) != expected_id
                    or authoritative[0].checksum != expected_checksum
                )
            )
        ):
            raise RubricError(
                "Speaking successor no longer matches its exact authoritative approval"
            )
        if authoritative:
            authoritative[0].status = SubjectiveAssessmentStatus.SUPERSEDED

    @classmethod
    def validate_approved_evidence(
        cls, session: Session, version: SubjectiveAssessmentVersion
    ) -> dict[str, Any]:
        """Validate the shared component shape, without authorizing a Speaking scoring run."""
        if (
            version.assessment_origin != "HUMAN"
            or version.status is not SubjectiveAssessmentStatus.APPROVED
        ):
            raise RubricError("Speaking component requires an approved human assessment")
        if any(
            (
                version.ai_run_id,
                version.workflow_version_id,
                version.agent_version_id,
                version.prompt_version_id,
                version.model_profile_id,
                version.ai_proposal,
            )
        ):
            raise RubricError("HUMAN Speaking component cannot contain AI provenance")
        rubric, revision = cls._pins(session, version)
        if any(
            row.status is not SubjectiveCriterionStatus.APPROVED
            or row.confidence is not None
            or row.ai_run_step_id is not None
            for row in version.criterion_assessments
        ):
            raise RubricError("approved Speaking criteria require human-only provenance")
        entries, values = cls._entries(
            session,
            version,
            rubric,
            revision,
            [
                {
                    "criterion_code": row.criterion_code,
                    "points": str(row.approved_points),
                    "level": row.approved_level,
                    "rationale": row.rationale,
                    "evidence": row.evidence,
                }
                for row in version.criterion_assessments
            ],
        )
        if version.approved_score != SubjectiveAssessmentService._aggregate(
            rubric, values
        ) or version.human_approved_values != {
            entry["criterion_code"]: {"level": entry["level"], "points": entry["points"]}
            for entry in entries
        }:
            raise RubricError("approved Speaking values do not match immutable criterion evidence")
        cls.validate_initial_approval(session, version)
        correction_id = version.provenance.get("result_correction_id")
        if correction_id is not None:
            from app.result_governance.service import ResultGovernanceService

            source = session.get(SubjectiveAssessmentVersion, version.supersedes_version_id)
            if source is None:
                raise RubricError("Speaking correction source is unavailable")
            try:
                pins = ResultGovernanceService.human_speaking_correction_pins(
                    session, source, uuid.UUID(str(correction_id)), phase="approved"
                )
            except ValueError as exc:
                raise RubricError(str(exc)) from exc
            if version.provenance.get("result_correction") != pins:
                raise RubricError("Speaking correction provenance no longer matches")
        return {
            "skill": "SPEAKING",
            "assessment_origin": "HUMAN",
            "evidence_mode": "AUDIO",
            "subjective_assessment_version_id": str(version.id),
            "subjective_assessment_checksum": version.checksum,
            "candidate_response_revision_id": str(revision.id),
            "response_checksum": version.response_checksum,
            "rubric_version_id": str(rubric.id),
            "rubric_checksum": version.rubric_checksum,
            "candidate_media_asset_id": str(version.candidate_media_asset_id),
            "audio_sha256": version.audio_sha256,
            "approved_score": str(version.approved_score),
        }

    @classmethod
    def approved_scoring_evidence(
        cls,
        session: Session,
        version: SubjectiveAssessmentVersion,
        attempt: CandidateExamAttempt,
        item: QuestionBankItemVersion,
        response: dict[str, Any],
        maximum: Decimal,
    ) -> dict[str, Any]:
        """Return the existing scorer contract after validating original AUDIO evidence."""
        if (
            item.skill is not QuestionBankSkill.SPEAKING
            or version.attempt_id != attempt.id
            or version.exam_version_id != attempt.exam_version_id
            or version.question_bank_item_version_id != item.id
            or version.assessment.organisation_id != attempt.organisation_id
            or version.assessment.candidate_user_id != attempt.candidate_user_id
            or str(version.exam_version_item_binding_id) != response.get("binding_id")
            or str(version.candidate_response_id) != response.get("response_id")
            or str(version.candidate_response_revision_id) != response.get("response_revision_id")
            or version.response_checksum != response.get("payload_checksum")
            or not maximum.is_finite()
            or maximum < 0
            or maximum != maximum.quantize(Decimal("0.0001"))
        ):
            raise RubricError("approved HUMAN Speaking does not match the exact scored response")
        component = cls.validate_approved_evidence(session, version)
        rubric = session.get(RubricVersion, version.rubric_version_id)
        if rubric is None:
            raise RubricError("Speaking rubric is unavailable")
        if cls.scoring_maximum(rubric) != maximum:
            raise RubricError("Speaking item maximum does not match the published rubric")
        aggregate = version.approved_score
        if (
            aggregate is None
            or not aggregate.is_finite()
            or not 0 <= aggregate <= maximum
            or aggregate != aggregate.quantize(Decimal("0.0001"))
        ):
            raise RubricError("approved Speaking aggregate is outside exact item bounds")
        approved = [
            {
                "criterion_code": row.criterion_code,
                "level": row.approved_level,
                "points": str(row.approved_points),
            }
            for row in version.criterion_assessments
        ]
        aggregate_checksum = canonical_checksum(
            {
                "rubric_checksum": version.rubric_checksum,
                "aggregation_policy": rubric.aggregation_policy,
                "approved_criteria": approved,
                "approved_score": str(aggregate),
                "maximum_points": str(maximum),
            }
        )
        return {
            "subjective_assessment_checksum": version.checksum,
            "component": {
                **component,
                "assessment_mode": "HUMAN_ONLY",
                "task_spec_id": str(rubric.task_spec_id),
                "maximum_points": str(maximum),
                "criterion_aggregation_checksum": aggregate_checksum,
            },
            "approved_criteria": approved,
            "audio_evidence": version.provenance["audio_evidence"],
            "criterion_aggregation_checksum": aggregate_checksum,
        }

    @staticmethod
    def scoring_maximum(rubric: RubricVersion) -> Decimal:
        """Derive the score scale only from the published aggregation definition."""
        bounds = {str(row["code"]): _decimal(row["max_points"]) for row in rubric.criteria}
        if rubric.aggregation_policy.get("method") in {"SUM", "WEIGHTED_SUM"}:
            maximum = SubjectiveAssessmentService._aggregate(rubric, bounds)
        elif rubric.aggregation_policy.get("method") == "DECLARED_MAPPING":
            mapping = rubric.aggregation_policy.get("mapping")
            if not isinstance(mapping, dict) or not mapping:
                raise RubricError("Speaking aggregation mapping must be explicitly declared")
            outputs = []
            for key, value in mapping.items():
                if not isinstance(key, str):
                    raise RubricError("Speaking aggregation mapping key is invalid")
                try:
                    pairs = [part.split(":", 1) for part in key.split(",")]
                    declared = {code: _decimal(points) for code, points in pairs}
                except (ValueError, TypeError) as exc:
                    raise RubricError("Speaking aggregation mapping key is invalid") from exc
                if (
                    len(pairs) != len(bounds)
                    or set(declared) != set(bounds)
                    or any(
                        not 0 <= points <= bounds[code]
                        or points != points.quantize(Decimal("0.0001"))
                        for code, points in declared.items()
                    )
                ):
                    raise RubricError(
                        "Speaking aggregation mapping must cover exact bounded criteria"
                    )
                output = _decimal(value)
                if output < 0 or output != output.quantize(Decimal("0.0001")):
                    raise RubricError("Speaking aggregation output must be finite fixed precision")
                outputs.append(output)
            maximum = max(outputs)
        else:
            raise RubricError("Speaking aggregation policy is unsupported")
        if maximum < 0 or not maximum.is_finite() or maximum != maximum.quantize(Decimal("0.0001")):
            raise RubricError("Speaking rubric maximum must have exact fixed precision")
        return maximum
