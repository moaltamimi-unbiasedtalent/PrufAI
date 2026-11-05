"use client";

import Link from "next/link";
import React, { FormEvent, useEffect, useRef, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import WritingFeedbackPanel from "../feedback-panel";
import { writingReviewApi, type WritingReview } from "../review-api";
import {
  writingFoundationApi,
  buildHumanCriteria,
  humanWritingActions,
} from "../foundation-api";
import {
  GovernanceError,
  HumanCriterionFields,
  RubricEvidenceView,
  type HumanCriterionEntry,
} from "../governance-ui";
import css from "../../assessment.module.css";
import {
  AIWritingExecutionEvidence,
  AIWritingReviewerFields,
  buildAIWritingCorrections,
  canReviewExecutedAIWriting,
  type AIReviewerCriterion,
} from "../ai-review";

export default function WritingReviewWorkspacePage({
  params,
}: {
  params: Promise<{ assessmentId: string }>;
}) {
  const { organisationId, permissions, user } = useAuth();
  const contextKey = `${user?.id ?? ""}:${organisationId ?? ""}:${[...permissions].sort().join(",")}`;
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  const [loadedContext, setLoadedContext] = useState<string | null>(null);
  const [storedReview, setReview] = useState<WritingReview | null>(null);
  const review = loadedContext === contextKey ? storedReview : null;
  const [history, setHistory] = useState<
    Array<{
      id: string;
      version_number: number;
      status: string;
      checksum: string;
    }>
  >([]);
  const [values, setValues] = useState<Record<string, HumanCriterionEntry>>({});
  const [reason, setReason] = useState("");
  const [aiValues, setAIValues] = useState<Record<string, AIReviewerCriterion>>(
    {},
  );
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const operations = useRef<Record<string, string>>({});
  const allowed = permissions.includes("subjective.read");
  useEffect(() => {
    let cancelled = false;
    setReview(null);
    setHistory([]);
    setError(null);
    setValues({});
    setAIValues({});
    setLoadedContext(null);
    if (!organisationId || !allowed) return;
    void params
      .then(async ({ assessmentId }) => {
        const [workspace, versions] = await Promise.all([
          writingReviewApi.detail(organisationId, assessmentId),
          writingReviewApi.history(organisationId, assessmentId),
        ]);
        if (!cancelled && currentContext.current === contextKey) {
          setReview(workspace);
          setHistory(versions.versions);
          setLoadedContext(contextKey);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled && currentContext.current === contextKey)
          setError(
            cause instanceof Error
              ? cause.message
              : "Writing evidence could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [contextKey, organisationId, allowed, params, reload]);
  useEffect(() => {
    operations.current = {};
    setReason("");
    setNotice(null);
    setBusy(false);
  }, [contextKey]);
  const human = review?.assessment_origin === "HUMAN";
  const legacyAI =
    review?.assessment_origin === "UNSPECIFIED" &&
    review.legacy_ai_review_eligible === true;
  const actions =
    review && user ? humanWritingActions(review, permissions, user.id) : null;
  const canPropose = actions?.propose ?? false;
  const canDecide = actions?.decide ?? false;
  const executedAI = Boolean(
    review?.execution_provenance || review?.assessment_mode === "AI_ASSISTED",
  );
  const canDecideAI = executedAI
    ? Boolean(
        review &&
          user &&
          canReviewExecutedAIWriting(review, permissions, user.id),
      )
    : (actions?.decideAI ?? false);
  async function propose(event: FormEvent) {
    event.preventDefault();
    if (!organisationId || !review?.rubric || !canPropose || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const requestedContext = contextKey;
    try {
      const criteria = buildHumanCriteria(
        review.rubric,
        values,
        review.candidate_text,
      );
      const key = `${review.version_id}:${review.checksum}:${JSON.stringify(criteria)}`;
      const operation = (operations.current[key] ??= crypto.randomUUID());
      await writingFoundationApi.propose(
        organisationId,
        review.version_id,
        review.checksum,
        criteria,
        operation,
      );
      if (currentContext.current !== requestedContext) return;
      setNotice(
        "Human criterion judgments submitted. An independent authorised reviewer must now decide this exact version.",
      );
      setReload((value) => value + 1);
    } catch (cause) {
      if (currentContext.current === requestedContext)
        setError(
          cause instanceof Error
            ? cause.message
            : "Human assessment could not be submitted. Refresh before continuing.",
        );
    } finally {
      if (currentContext.current === requestedContext) setBusy(false);
    }
  }
  async function decide(
    decision: "APPROVED" | "REJECTED" | "CHANGES_REQUESTED",
  ) {
    if (!organisationId || !review || busy || (!canDecide && !canDecideAI))
      return;
    if ((executedAI || (decision !== "APPROVED" && human)) && !reason.trim()) {
      setError("A decision reason is required.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    const requestedContext = contextKey;
    try {
      if (human) {
        const key = `${review.version_id}:${review.checksum}:${decision}:${reason.trim()}`;
        const operation = (operations.current[key] ??= crypto.randomUUID());
        if (decision === "APPROVED")
          await writingFoundationApi.approve(
            organisationId,
            review.version_id,
            review.checksum,
            operation,
          );
        else
          await writingFoundationApi.decide(
            organisationId,
            review.version_id,
            review.checksum,
            decision,
            reason.trim(),
            operation,
          );
      } else if (executedAI) {
        const corrections =
          decision === "APPROVED" && review.rubric
            ? buildAIWritingCorrections(review.rubric, aiValues)
            : undefined;
        const key = `${review.version_id}:${review.checksum}:${decision}:${reason.trim()}:${JSON.stringify(corrections)}`;
        const operation = (operations.current[key] ??= crypto.randomUUID());
        const governance = {
          operation_id: operation,
          expected_checksum: review.checksum,
          reason: reason.trim(),
          ...(corrections ? { corrections } : {}),
        };
        if (decision === "APPROVED")
          await writingReviewApi.approve(
            organisationId,
            review.version_id,
            governance,
          );
        else
          await writingReviewApi.decide(
            organisationId,
            review.version_id,
            decision,
            governance,
          );
      } else if (decision === "APPROVED")
        await writingReviewApi.approve(organisationId, review.version_id);
      else
        await writingReviewApi.decide(
          organisationId,
          review.version_id,
          decision,
        );
      if (currentContext.current !== requestedContext) return;
      setNotice(
        "The authorised decision was recorded. Inspect the persisted status and provenance below.",
      );
      setReload((value) => value + 1);
    } catch (cause) {
      if (currentContext.current === requestedContext)
        setError(
          cause instanceof Error
            ? cause.message
            : "The decision could not be confirmed. Refresh before continuing.",
        );
    } finally {
      if (currentContext.current === requestedContext) setBusy(false);
    }
  }
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/writing-reviews">← Writing review queue</Link>
      </p>
      <h1>Writing assessment review</h1>
      {!allowed || !organisationId ? (
        <p role="alert">
          Subjective read permission in the selected institution is required.
        </p>
      ) : (
        <>
          {error && (
            <GovernanceError
              error={error}
              retry={() => setReload((value) => value + 1)}
            />
          )}
          {notice && (
            <p className={css.notice} role="status">
              {notice}
            </p>
          )}
          {!review && !error && (
            <p role="status">Loading exact Writing assessment evidence…</p>
          )}
          {review && (
            <>
              <p>
                <strong>Assessment origin:</strong>{" "}
                {review.assessment_origin ?? "UNSPECIFIED"}
              </p>
              <p>
                <strong>Assessment status:</strong> {review.status}
              </p>
              {review.assessment_mode && (
                <p>Assessment mode: {review.assessment_mode}</p>
              )}
              <p className={css.notice}>
                {human
                  ? "This is a HUMAN assessment. No AI proposal or model confidence is generated or used by this workflow."
                  : legacyAI
                    ? "Legacy AI-associated record; execution authenticity not established. Existing authorised review controls remain available without reclassifying its stored origin."
                    : review.assessment_origin === "AI"
                      ? `AI proposal provenance: ${review.ai_run_id ?? "Not exposed in this historical record"}. An authorised human must decide it.`
                      : "Assessment origin is not specified in this historical record. No AI execution is inferred."}
              </p>
              {executedAI && <AIWritingExecutionEvidence review={review} />}
              <section className={css.card}>
                <h2>{review.task_label ?? "Exact Writing task"}</h2>
                <p>
                  {review.task_prompt_available === false
                    ? "Question Bank read permission is required to inspect the task prompt."
                    : review.task_prompt ||
                      "Task prompt is not available in this historical projection."}
                </p>
                <h2>Submitted candidate Writing</h2>
                <p style={{ whiteSpace: "pre-wrap" }}>
                  {review.candidate_text}
                </p>
              </section>
              <dl className={css.provenance}>
                <dt>Assessment</dt>
                <dd>{review.id}</dd>
                <dt>Exact assessment version</dt>
                <dd>{review.version_id}</dd>
                <dt>Version checksum</dt>
                <dd>{review.checksum}</dd>
                <dt>Submitted response revision</dt>
                <dd>
                  {review.candidate_response_revision_id ?? "Unavailable"}
                </dd>
                <dt>Response checksum</dt>
                <dd>{review.response_checksum ?? "Unavailable"}</dd>
                <dt>Item version</dt>
                <dd>{review.question_bank_item_version_id ?? "Unavailable"}</dd>
                <dt>Standard Pack version</dt>
                <dd>{review.standard_pack_version_id ?? "Unavailable"}</dd>
                <dt>Human assessor</dt>
                <dd>{review.assessor_id ?? "Not submitted"}</dd>
                <dt>Human assessment submitted</dt>
                <dd>{review.provenance?.assessed_at ?? "Not submitted"}</dd>
                <dt>Decision reviewer</dt>
                <dd>{review.reviewer_id ?? "Not decided"}</dd>
                <dt>Decision recorded</dt>
                <dd>{review.provenance?.reviewed_at ?? "Not decided"}</dd>
                <dt>Review checksum</dt>
                <dd>{review.review?.reviewed_checksum ?? "Not requested"}</dd>
              </dl>
              {review.rubric && <RubricEvidenceView rubric={review.rubric} />}
              <WritingFeedbackPanel
                artifact={{ id: review.version_id, checksum: review.checksum }}
                status={review.status}
              />
              {canPropose && review.rubric && (
                <form
                  className={css.form}
                  onSubmit={(event) => void propose(event)}
                >
                  <h2>Enter human criterion judgments</h2>
                  <p>
                    Enter each criterion using the published rubric. Optional
                    quotes must match the submitted response exactly; an empty
                    optional evidence field creates no citation.
                  </p>
                  <HumanCriterionFields
                    criteria={review.rubric.criteria}
                    levels={review.rubric.performance_levels}
                    values={values}
                    disabled={busy}
                    evidenceRequired={
                      review.rubric.evidence_policy.required === true
                    }
                    onChange={(code, value) =>
                      setValues((current) => ({ ...current, [code]: value }))
                    }
                  />
                  <button disabled={busy}>
                    Submit human assessment for independent review
                  </button>
                </form>
              )}
              {review.criteria.length > 0 && (
                <section className={css.card}>
                  <h2>
                    {human
                      ? "Recorded human criterion judgments"
                      : "Recorded proposal criteria"}
                  </h2>
                  {review.criteria.map((criterion) => (
                    <article key={String(criterion.criterion_code)}>
                      <h3>{String(criterion.criterion_code)}</h3>
                      <p>
                        Proposed level:{" "}
                        {String(criterion.proposed_level ?? "Not recorded")} ·
                        Points:{" "}
                        {String(criterion.proposed_points ?? "Not recorded")}
                      </p>
                      <p>
                        Rationale:{" "}
                        {String(criterion.rationale ?? "Not recorded")}
                      </p>
                      {Array.isArray(criterion.evidence) &&
                        criterion.evidence.length > 0 && (
                          <details>
                            <summary>
                              Exact response evidence references
                            </summary>
                            <pre className={css.evidence}>
                              {JSON.stringify(criterion.evidence, null, 2)}
                            </pre>
                          </details>
                        )}
                      <p>
                        Approved level:{" "}
                        {String(criterion.approved_level ?? "Not approved")} ·
                        Points:{" "}
                        {String(criterion.approved_points ?? "Not approved")}
                      </p>
                      {!human && (
                        <p>
                          Model confidence:{" "}
                          {review.execution_provenance?.test_only
                            ? "Not established — TEST ONLY execution"
                            : String(criterion.confidence ?? "Unavailable")}
                        </p>
                      )}
                    </article>
                  ))}
                </section>
              )}
              {human &&
                review.status === "IN_REVIEW" &&
                review.assessor_id === user?.id && (
                  <p className={css.notice}>
                    Your human judgments are awaiting an independent reviewer.
                    You cannot approve or reject your own submitted assessment.
                  </p>
                )}
              {(canDecide || canDecideAI) && (
                <section className={css.card}>
                  <h2>Authorised review decision</h2>
                  <p>
                    Decide only after inspecting the exact response, rubric and
                    criterion judgments above. The server enforces the current
                    checksum and actor permissions.
                  </p>
                  {executedAI && review.rubric && (
                    <AIWritingReviewerFields
                      rubric={review.rubric}
                      values={aiValues}
                      disabled={busy}
                      onChange={(code, value) =>
                        setAIValues((current) => ({
                          ...current,
                          [code]: value,
                        }))
                      }
                    />
                  )}
                  {(human || executedAI) && (
                    <label className={css.form}>
                      {executedAI
                        ? "Human review decision reason"
                        : "Decision reason for rejection or changes"}
                      <textarea
                        rows={3}
                        maxLength={2000}
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                      />
                    </label>
                  )}
                  <div className={css.actions}>
                    <button
                      disabled={busy}
                      onClick={() => void decide("APPROVED")}
                    >
                      Approve{" "}
                      {human
                        ? "human Writing assessment"
                        : "AI Writing proposal"}
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void decide("CHANGES_REQUESTED")}
                    >
                      Request Writing assessment changes
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void decide("REJECTED")}
                    >
                      Reject Writing assessment
                    </button>
                  </div>
                </section>
              )}
              {review.status === "APPROVED" && (
                <p className={css.notice}>
                  Approved subjective score:{" "}
                  {review.approved_score ?? "See criterion evidence"}. This
                  version is immutable and may be consumed by the existing
                  scoring service.{" "}
                  {permissions.includes("exam_assignment.read") && (
                    <Link
                      href={
                        review.attempt_id
                          ? `/institution/overview#attempt-${encodeURIComponent(review.attempt_id)}`
                          : "/institution/overview"
                      }
                    >
                      Inspect submitted attempt and scoring →
                    </Link>
                  )}
                </p>
              )}
              {[
                "REJECTED",
                "CHANGES_REQUESTED",
                "SUPERSEDED",
                "VOIDED",
                "FAILED",
              ].includes(review.status) && (
                <p className={css.notice}>
                  This is a historical or terminal assessment state. Existing
                  evidence cannot be overwritten from this screen.
                </p>
              )}
              <details className={css.card}>
                <summary>Immutable assessment version history</summary>
                <ul>
                  {history.map((version) => (
                    <li key={version.id}>
                      Version {version.version_number}: {version.status} ·{" "}
                      {version.id} · {version.checksum}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </>
      )}
    </section>
  );
}
