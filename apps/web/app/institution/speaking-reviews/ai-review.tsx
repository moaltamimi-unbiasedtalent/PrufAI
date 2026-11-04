"use client";

import React, { useRef, useState } from "react";
import { authenticatedFetch } from "../../../lib/api";
import { AssessmentApiError } from "../assessment-api";
import type { SpeakingReview, AudioEvidenceAsset } from "./review-api";
import type { RubricEvidence } from "../writing-reviews/governance-ui";
import { supportedSpeakingExecutor } from "./execution-api";
import { buildHumanAudioCriteria } from "./foundation-api";
import {
  HumanAudioCriterionFields,
  type HumanAudioCriterionEntry,
} from "./human-criteria";
import css from "../assessment.module.css";

export function verifiedAISpeakingProvenance(
  review: SpeakingReview,
  audio: AudioEvidenceAsset,
) {
  const execution = review.execution_provenance;
  return Boolean(
    review.assessment_origin === "AI" &&
      review.assessment_mode === "AI_ASSISTED" &&
      review.version_id &&
      review.version_id === review.assessment_version_id &&
      review.evidence_mode === "AUDIO" &&
      review.original_audio_primary_evidence === true &&
      review.derived_transcription === false &&
      review.speech_transcription_version_id === null &&
      review.transcript_checksum === null &&
      review.rubric &&
      review.rubric_checksum &&
      review.candidate_response_revision_id &&
      review.response_checksum &&
      review.checksum &&
      execution?.execution_status === "COMPLETED" &&
      execution.ai_run_status === "SUCCEEDED" &&
      execution.ai_run_id === review.ai_run_id &&
      execution.evidence_mode === "AUDIO" &&
      execution.speech_transcription_version_id === null &&
      execution.transcript_checksum === null &&
      execution.candidate_response_revision_id ===
        review.candidate_response_revision_id &&
      execution.response_checksum === review.response_checksum &&
      execution.rubric_version_id === review.rubric.id &&
      execution.rubric_checksum === review.rubric.checksum &&
      execution.rubric_checksum === review.rubric_checksum &&
      execution.candidate_media_asset_id === review.candidate_media_asset_id &&
      execution.candidate_media_asset_id === audio.media_asset_id &&
      execution.audio_sha256 === review.audio_sha256 &&
      execution.audio_sha256 === audio.sha256 &&
      execution.candidate_response_revision_id ===
        (audio.candidate_response_revision_id ?? audio.response_revision_id) &&
      review.attempt_id === audio.attempt_id &&
      execution.attempt_id === review.attempt_id &&
      execution.exam_version_id === audio.exam_version_id &&
      execution.exam_version_id === review.exam_version_id &&
      execution.exam_version_item_binding_id === audio.binding_id &&
      execution.exam_version_item_binding_id ===
        review.exam_version_item_binding_id &&
      execution.question_bank_item_version_id === audio.item_version_id &&
      execution.question_bank_item_version_id ===
        review.question_bank_item_version_id &&
      execution.standard_pack_version_id === review.standard_pack_version_id &&
      execution.standard_pack_checksum === review.standard_pack_checksum &&
      execution.standard_pack_version_id &&
      execution.standard_pack_checksum &&
      Number.isSafeInteger(execution.duration_ms) &&
      execution.duration_ms > 0 &&
      execution.duration_ms === audio.duration_ms &&
      execution.recording_policy_checksum &&
      execution.recording_policy_checksum ===
        review.recording_policy_checksum &&
      execution.assessment_policy_checksum &&
      execution.assessment_policy_checksum ===
        review.assessment_policy_checksum &&
      execution.execution_job_id &&
      execution.execution_profile_id &&
      execution.profile_version &&
      execution.model_profile_id &&
      execution.model_version &&
      execution.prompt_version_id &&
      execution.prompt_checksum &&
      execution.agent_version_id &&
      execution.agent_checksum &&
      execution.workflow_version_id &&
      execution.workflow_checksum &&
      supportedSpeakingExecutor(execution),
  );
}

export function canReviewExecutedAISpeaking(
  review: SpeakingReview,
  audio: AudioEvidenceAsset,
  permissions: string[],
  userId: string,
) {
  return Boolean(
    verifiedAISpeakingProvenance(review, audio) &&
      review.is_current_version !== false &&
      review.status === "AI_PROPOSED" &&
      permissions.includes("subjective.review") &&
      review.human_review_policy?.required === true &&
      typeof review.human_review_policy.separation_of_duties === "boolean" &&
      review.review?.status === "PENDING" &&
      review.review.reviewed_checksum === review.checksum &&
      review.review.requested_by &&
      (review.human_review_policy.separation_of_duties === false ||
        review.review.requested_by !== userId),
  );
}

export function buildAISpeakingCorrections(
  rubric: RubricEvidence,
  values: Record<string, HumanAudioCriterionEntry>,
  audio: AudioEvidenceAsset,
  revisionId: string,
) {
  const minimum = rubric.evidence_policy.minimum_references;
  if (
    rubric.evidence_policy.mode !== "AUDIO" ||
    rubric.evidence_policy.required !== true ||
    rubric.evidence_policy.time_unit !== "milliseconds" ||
    typeof minimum !== "number" ||
    !Number.isSafeInteger(minimum) ||
    minimum < 1 ||
    minimum > 10
  )
    throw new Error(
      "A published bounded original-audio evidence policy is required.",
    );
  const expected = new Set(rubric.criteria.map((criterion) => criterion.code));
  if (Object.keys(values).some((code) => !expected.has(code)))
    throw new Error(
      "Human decisions must use only the exact published rubric criteria.",
    );
  const criteria = buildHumanAudioCriteria(rubric, values, {
    media_asset_id: audio.media_asset_id,
    sha256: audio.sha256,
    duration_ms: audio.duration_ms,
    candidate_response_revision_id: revisionId,
  });
  if (criteria.some((criterion) => criterion.evidence.length < minimum))
    throw new Error(
      `The published rubric requires at least ${minimum} original-audio references per criterion.`,
    );
  return Object.fromEntries(
    criteria.map((entry) => [
      entry.criterion_code,
      {
        approved_points: entry.points,
        approved_level: entry.level,
        rationale: entry.rationale,
        evidence: entry.evidence.map((span) => ({
          type: "AUDIO" as const,
          candidate_media_asset_id: span.asset_id,
          audio_sha256: span.audio_sha256,
          start_ms: span.start_ms,
          end_ms: span.end_ms,
        })),
      },
    ]),
  );
}

export type AISpeakingDecision = {
  operation_id: string;
  expected_checksum: string;
  reason: string;
  corrections?: ReturnType<typeof buildAISpeakingCorrections>;
};
export const speakingAIReviewApi = {
  async decide(
    org: string,
    version: string,
    decision: "APPROVED" | "REJECTED" | "CHANGES_REQUESTED",
    payload: AISpeakingDecision,
  ) {
    const response = await authenticatedFetch(
      `/api/v1/organisations/${org}/subjective-assessments/${version}/${decision === "APPROVED" ? "approve" : "decision"}`,
      {
        method: "POST",
        body: JSON.stringify(
          decision === "APPROVED" ? payload : { ...payload, decision },
        ),
      },
    );
    if (!response.ok) {
      let detail: string | undefined;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        /* Keep actual HTTP failure. */
      }
      throw new AssessmentApiError(response.status, detail);
    }
    return (await response.json()) as unknown;
  },
};

export function AISpeakingExecutionEvidence({
  review,
  audio,
}: {
  review: SpeakingReview;
  audio: AudioEvidenceAsset;
}) {
  const execution = review.execution_provenance;
  if (!execution || !verifiedAISpeakingProvenance(review, audio))
    return (
      <p role="alert">
        A matching completed Speaking execution is not verified for this exact
        response, rubric and original audio. No authentic AI completion is
        inferred.
      </p>
    );
  return (
    <section
      className={css.card}
      aria-label="Exact AI Speaking execution provenance"
    >
      <h2>Originating AI-assisted audio execution</h2>
      {execution.test_only ? (
        <p className={css.notice}>
          TEST ONLY AI PROPOSAL · Deterministic technical output. This does not
          establish authentic speech understanding, acoustic relevance,
          pronunciation, fluency or German Speaking scoring accuracy.
        </p>
      ) : (
        <p>
          Machine-generated audio proposals remain subject to an authorized
          human decision. Completion does not approve a grade, score, result or
          release.
        </p>
      )}
      <p>
        AUDIO evidence only. No transcript was used or requested by this
        execution. Proposed audio spans are model suggestions: listen to the
        original audio and independently verify acoustic relevance for every
        criterion.
      </p>
      <dl className={css.provenance}>
        <dt>Completed execution job</dt>
        <dd>{execution.execution_job_id}</dd>
        <dt>AI run</dt>
        <dd>
          {execution.ai_run_id} · {execution.ai_run_status}
        </dd>
        <dt>Pinned execution profile</dt>
        <dd>{execution.profile_version}</dd>
        <dt>Provider / model / adapter</dt>
        <dd>
          {execution.provider} / {execution.model_name} /{" "}
          {execution.adapter_version}
        </dd>
        <dt>Exact model configuration</dt>
        <dd>
          {execution.model_profile_id} · {execution.model_version}
        </dd>
        <dt>Exact prompt version</dt>
        <dd>
          {execution.prompt_version_id} · {execution.prompt_checksum}
        </dd>
        <dt>Exact agent version</dt>
        <dd>
          {execution.agent_version_id} · {execution.agent_checksum}
        </dd>
        <dt>Exact workflow version</dt>
        <dd>
          {execution.workflow_version_id} · {execution.workflow_checksum}
        </dd>
        <dt>Verified original audio duration</dt>
        <dd>{execution.duration_ms} ms</dd>
        <dt>Recording policy checksum</dt>
        <dd>{execution.recording_policy_checksum}</dd>
        <dt>Assessment policy checksum</dt>
        <dd>{execution.assessment_policy_checksum}</dd>
        <dt>Human review request</dt>
        <dd>
          {review.review?.id ?? "Not exposed"} ·{" "}
          {review.review?.status ?? "Not verified"}
        </dd>
        <dt>Human review checksum</dt>
        <dd>{review.review?.reviewed_checksum ?? "Not verified"}</dd>
      </dl>
    </section>
  );
}

export function AISpeakingReviewControls({
  review,
  audio,
  organisationId,
  permissions,
  userId,
  onPersist,
}: {
  review: SpeakingReview;
  audio: AudioEvidenceAsset;
  organisationId: string;
  permissions: string[];
  userId: string;
  onPersist: () => void;
}) {
  const [values, setValues] = useState<
    Record<string, HumanAudioCriterionEntry>
  >({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operations = useRef<Record<string, string>>({});
  if (
    !canReviewExecutedAISpeaking(review, audio, permissions, userId) ||
    !review.rubric ||
    !review.checksum
  )
    return null;
  async function decide(
    decision: "APPROVED" | "REJECTED" | "CHANGES_REQUESTED",
  ) {
    if (
      busy ||
      !reason.trim() ||
      !review.checksum ||
      !review.rubric ||
      !review.version_id ||
      !canReviewExecutedAISpeaking(review, audio, permissions, userId)
    ) {
      setError(
        "A private independent decision reason and exact current evidence are required.",
      );
      return;
    }
    setError(null);
    try {
      const corrections =
        decision === "APPROVED"
          ? buildAISpeakingCorrections(
              review.rubric,
              values,
              audio,
              review.candidate_response_revision_id!,
            )
          : undefined;
      const key = JSON.stringify([
        review.version_id,
        review.checksum,
        decision,
        reason.trim(),
        corrections,
      ]);
      setBusy(true);
      await speakingAIReviewApi.decide(
        organisationId,
        review.version_id,
        decision,
        {
          operation_id: (operations.current[key] ??= crypto.randomUUID()),
          expected_checksum: review.checksum,
          reason: reason.trim(),
          ...(corrections ? { corrections } : {}),
        },
      );
      onPersist();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The decision could not be confirmed. Refresh before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className={css.card}
      aria-label="Independent AI Speaking proposal review"
    >
      <h2>Independent human review of AI Speaking proposal</h2>
      <p>
        Listen to the original recording, inspect the exact published rubric and
        independently judge each proposed audio span. Your points, level,
        rationale and audio references start blank and may differ from the AI
        values. An approval requires a complete human decision for every
        criterion.
      </p>
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      <HumanAudioCriterionFields
        criteria={review.rubric.criteria}
        levels={review.rubric.performance_levels}
        durationMs={audio.duration_ms}
        values={values}
        disabled={busy}
        onChange={(code, value) =>
          setValues((previous) => ({ ...previous, [code]: value }))
        }
      />
      <label className={css.form}>
        Private AI Speaking review decision reason
        <textarea
          rows={3}
          maxLength={2000}
          value={reason}
          disabled={busy}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <div className={css.actions}>
        <button
          disabled={busy || !reason.trim()}
          onClick={() => void decide("APPROVED")}
        >
          Approve AI Speaking proposal
        </button>
        <button
          disabled={busy || !reason.trim()}
          onClick={() => void decide("CHANGES_REQUESTED")}
        >
          Request AI Speaking changes
        </button>
        <button
          disabled={busy || !reason.trim()}
          onClick={() => void decide("REJECTED")}
        >
          Reject AI Speaking proposal
        </button>
      </div>
    </section>
  );
}
