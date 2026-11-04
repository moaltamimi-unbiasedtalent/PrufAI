"use client";

import React from "react";
import type { WritingReview } from "./review-api";
import type { RubricEvidence } from "./governance-ui";
import css from "../assessment.module.css";

export type AIReviewerCriterion = { points: string; level: string };

export function verifiedAIWritingProvenance(review: WritingReview) {
  const execution = review.execution_provenance;
  return Boolean(
    review.assessment_origin === "AI" &&
      review.assessment_mode === "AI_ASSISTED" &&
      review.rubric &&
      review.candidate_response_revision_id &&
      review.response_checksum &&
      review.rubric_checksum &&
      execution?.execution_status === "COMPLETED" &&
      execution.ai_run_status === "SUCCEEDED" &&
      execution.ai_run_id === review.ai_run_id &&
      execution.candidate_response_revision_id ===
        review.candidate_response_revision_id &&
      execution.response_checksum === review.response_checksum &&
      execution.rubric_version_id === review.rubric?.id &&
      execution.rubric_checksum === review.rubric?.checksum &&
      execution.rubric_checksum === review.rubric_checksum &&
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
      typeof execution.test_only === "boolean" &&
      ["OPENAI", "DETERMINISTIC_TEST"].includes(execution.provider) &&
      execution.test_only === (execution.provider === "DETERMINISTIC_TEST"),
  );
}

export function canReviewExecutedAIWriting(
  review: WritingReview,
  permissions: string[],
  userId: string,
) {
  return Boolean(
    verifiedAIWritingProvenance(review) &&
      review.status === "AI_PROPOSED" &&
      permissions.includes("subjective.review") &&
      review.human_review_policy?.required === true &&
      review.review?.status === "PENDING" &&
      review.review.reviewed_checksum === review.checksum &&
      review.review.requested_by &&
      (review.human_review_policy.separation_of_duties === false ||
        review.review.requested_by !== userId),
  );
}

export function buildAIWritingCorrections(
  rubric: RubricEvidence,
  values: Record<string, AIReviewerCriterion>,
) {
  const expected = new Set(rubric.criteria.map((criterion) => criterion.code));
  if (Object.keys(values).some((code) => !expected.has(code)))
    throw new Error(
      "Reviewer values must use only the exact published rubric criteria.",
    );
  return Object.fromEntries(
    rubric.criteria.map((criterion) => {
      const entry = values[criterion.code];
      if (!entry || entry.points === "" || !entry.level)
        throw new Error(
          `Record your human decision for ${criterion.code}; AI values are not copied automatically.`,
        );
      if (
        !Number.isFinite(Number(entry.points)) ||
        Number(entry.points) < 0 ||
        Number(entry.points) > Number(criterion.max_points)
      )
        throw new Error(
          `Reviewer points for ${criterion.code} must stay within the published rubric bounds.`,
        );
      if (
        !rubric.performance_levels.some((level) => level.code === entry.level)
      )
        throw new Error(
          `Choose a published rubric performance level for ${criterion.code}.`,
        );
      return [
        criterion.code,
        { approved_points: entry.points, approved_level: entry.level },
      ];
    }),
  );
}

export function AIWritingExecutionEvidence({
  review,
}: {
  review: WritingReview;
}) {
  const execution = review.execution_provenance;
  if (!verifiedAIWritingProvenance(review) || !execution)
    return (
      <p role="alert">
        A matching completed Writing execution is not verified for this response
        and rubric. No authentic AI completion is inferred.
      </p>
    );
  return (
    <section
      className={css.card}
      aria-label="Exact AI Writing execution provenance"
    >
      <h2>Originating AI-assisted execution</h2>
      {execution.test_only ? (
        <p className={css.notice}>
          TEST ONLY AI PROPOSAL · Deterministic technical output, not authentic
          model inference or German Writing scoring accuracy.
        </p>
      ) : (
        <p>
          Machine-generated criterion proposals require an authorized human
          decision. Execution completion is not final grade approval.
        </p>
      )}
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

export function AIWritingReviewerFields({
  rubric,
  values,
  onChange,
  disabled,
}: {
  rubric: RubricEvidence;
  values: Record<string, AIReviewerCriterion>;
  onChange: (code: string, value: AIReviewerCriterion) => void;
  disabled: boolean;
}) {
  return (
    <>
      <h3>Record your human criterion decisions</h3>
      <p>
        Inspect the submitted Writing, rubric, evidence and AI proposals. Select
        each permitted level and enter your points. These fields start blank and
        may differ from the AI values.
      </p>
      {rubric.criteria.map((criterion) => {
        const value = values[criterion.code] ?? { points: "", level: "" };
        return (
          <fieldset key={criterion.code} disabled={disabled}>
            <legend>{criterion.code} · human reviewer decision</legend>
            <label className={css.form}>
              Reviewer points for {criterion.code}
              <input
                type="number"
                min="0"
                max={criterion.max_points}
                step="any"
                value={value.points}
                onChange={(event) =>
                  onChange(criterion.code, {
                    ...value,
                    points: event.target.value,
                  })
                }
              />
            </label>
            <label className={css.form}>
              Reviewer level for {criterion.code}
              <select
                value={value.level}
                onChange={(event) =>
                  onChange(criterion.code, {
                    ...value,
                    level: event.target.value,
                  })
                }
              >
                <option value="">Select your rubric-based judgment</option>
                {rubric.performance_levels.map((level) => (
                  <option key={level.code} value={level.code}>
                    {level.code} · {level.label ?? level.name ?? level.code}
                  </option>
                ))}
              </select>
            </label>
          </fieldset>
        );
      })}
    </>
  );
}
