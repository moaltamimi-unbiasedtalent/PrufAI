"use client";

import Link from "next/link";
import React, { FormEvent, useEffect, useRef, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import {
  speakingReviewApi,
  type SpeakingReview,
  type AudioEvidenceAsset,
} from "../review-api";
import {
  AISpeakingExecutionEvidence,
  AISpeakingReviewControls,
} from "../ai-review";
import { AudioEvidenceView } from "../audio-evidence-view";
import { TranscriptionEvidencePanel } from "../transcription-evidence";
import {
  speakingFoundationApi,
  buildHumanAudioCriteria,
} from "../foundation-api";
import {
  HumanAudioCriterionFields,
  type HumanAudioCriterionEntry,
} from "../human-criteria";
import {
  GovernanceError,
  RubricEvidenceView,
} from "../../writing-reviews/governance-ui";
import { humanWritingActions } from "../../writing-reviews/foundation-api";
import css from "../../assessment.module.css";

export default function SpeakingReviewWorkspace({
  params,
}: {
  params: Promise<{ assessmentId: string }>;
}) {
  const { organisationId, user, permissions } = useAuth();
  const context =
    user && organisationId && permissions.includes("subjective.read")
      ? JSON.stringify([user.id, organisationId, [...permissions].sort()])
      : null;
  const [snapshot, setSnapshot] = useState<{
    context: string;
    review: SpeakingReview;
    audio: AudioEvidenceAsset;
    assessmentId: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [versionId, setVersionId] = useState("");
  const [values, setValues] = useState<
    Record<string, HumanAudioCriterionEntry>
  >({});
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const operations = useRef<Record<string, string>>({});
  useEffect(() => {
    setSnapshot(null);
    setError(null);
    setValues({});
    setReason("");
    if (!context || !organisationId) return;
    const abort = new AbortController();
    void params
      .then(async ({ assessmentId }) => {
        const review = await speakingReviewApi.detail(
          organisationId,
          assessmentId,
          abort.signal,
          versionId || undefined,
        );
        const audio =
          review.audio ??
          (await speakingReviewApi.audioDetail(
            organisationId,
            review.candidate_media_asset_id,
            abort.signal,
          ));
        if (
          audio.media_asset_id !== review.candidate_media_asset_id ||
          (review.audio_sha256 && audio.sha256 !== review.audio_sha256)
        )
          throw new Error(
            "The recording does not match this exact assessment version.",
          );
        if (!abort.signal.aborted)
          setSnapshot({ context, review, audio, assessmentId });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "The exact assessment evidence could not be loaded.",
          );
      });
    return () => abort.abort();
  }, [context, organisationId, params, refresh, versionId]);
  const data = snapshot?.context === context ? snapshot : null;
  const review = data?.review;
  const human = review?.assessment_origin === "HUMAN";
  const executedAI =
    review?.assessment_origin === "AI" && Boolean(review.execution_provenance);
  const current = review?.is_current_version !== false;
  const exactVersion = review?.version_id ?? review?.assessment_version_id;
  const taskPrompt =
    typeof review?.task_prompt === "string"
      ? review.task_prompt
      : typeof review?.task_prompt?.prompt === "string"
        ? review.task_prompt.prompt
        : null;
  const actions =
    review && user
      ? humanWritingActions(
          {
            ...review,
            assessment_origin: review.assessment_origin ?? "UNSPECIFIED",
          },
          permissions,
          user.id,
        )
      : null;
  const canPropose = human && current && actions?.propose;
  const canDecide = human && current && actions?.decide;
  const canSucceed =
    human &&
    current &&
    ["APPROVED", "REJECTED", "CHANGES_REQUESTED"].includes(
      review?.status ?? "",
    ) &&
    permissions.includes("subjective.assess");
  function operation(key: string) {
    return (operations.current[`${context}:${key}`] ??= crypto.randomUUID());
  }
  async function propose(event: FormEvent) {
    event.preventDefault();
    if (
      !organisationId ||
      !data ||
      !review?.rubric ||
      !review.checksum ||
      !exactVersion ||
      !canPropose ||
      busy
    )
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const criteria = buildHumanAudioCriteria(review.rubric, values, {
        media_asset_id: data.audio.media_asset_id,
        sha256: data.audio.sha256,
        duration_ms: data.audio.duration_ms,
        candidate_response_revision_id:
          review.candidate_response_revision_id ??
          data.audio.candidate_response_revision_id ??
          data.audio.response_revision_id ??
          "",
      });
      await speakingFoundationApi.propose(
        organisationId,
        exactVersion,
        review.checksum,
        criteria,
        operation(
          `${exactVersion}:${review.checksum}:propose:${JSON.stringify(criteria)}`,
        ),
      );
      setNotice(
        "Human audio judgments submitted. A distinct authorised reviewer must decide this exact version.",
      );
      setRefresh((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The judgment could not be confirmed. Refresh before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function decide(
    decision: "APPROVED" | "REJECTED" | "CHANGES_REQUESTED",
  ) {
    if (
      !organisationId ||
      !review?.checksum ||
      !exactVersion ||
      !canDecide ||
      busy
    )
      return;
    if (decision !== "APPROVED" && !reason.trim()) {
      setError("A decision reason is required.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const op = operation(
        `${exactVersion}:${review.checksum}:${decision}:${reason.trim()}`,
      );
      if (decision === "APPROVED")
        await speakingFoundationApi.approve(
          organisationId,
          exactVersion,
          review.checksum,
          op,
        );
      else
        await speakingFoundationApi.decide(
          organisationId,
          exactVersion,
          review.checksum,
          decision,
          reason.trim(),
          op,
        );
      setNotice(
        "The independent human decision was persisted. Inspect its exact status and provenance below.",
      );
      setRefresh((value) => value + 1);
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
  async function successor(event: FormEvent) {
    event.preventDefault();
    if (
      !organisationId ||
      !review?.checksum ||
      !exactVersion ||
      !canSucceed ||
      !reason.trim() ||
      busy
    )
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await speakingFoundationApi.successor(
        organisationId,
        exactVersion,
        review.checksum,
        reason.trim(),
        operation(
          `${exactVersion}:${review.checksum}:successor:${reason.trim()}`,
        ),
      );
      setVersionId("");
      setRefresh((value) => value + 1);
      setNotice(
        "A successor assessment version was created for a new human judgment and independent decision. The prior approved evidence remains immutable.",
      );
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The successor could not be confirmed. Refresh before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/speaking-reviews">
          ← Speaking review queue
        </Link>
      </p>
      <h1>Speaking assessment review</h1>
      {!context ? (
        <p role="alert">
          Subjective read permission in the selected institution is required.
        </p>
      ) : (
        <>
          {error && (
            <GovernanceError
              error={error}
              retry={() => setRefresh((value) => value + 1)}
            />
          )}
          {notice && (
            <p className={css.notice} role="status">
              {notice}
            </p>
          )}
          {!data && !error && (
            <p role="status">Loading exact Speaking evidence…</p>
          )}
          {data && review && organisationId && (
            <>
              <p>
                <strong>Assessment origin:</strong>{" "}
                {review.assessment_origin ?? "UNSPECIFIED"}
              </p>
              <p>
                <strong>Assessment status:</strong> {review.status}
              </p>
              {human ? (
                <p className={css.notice}>
                  HUMAN_ONLY · AUDIO. Human judgments use the verified original
                  recording. No transcript, AI proposal, confidence or model
                  attribution is generated by this workflow.
                </p>
              ) : executedAI ? (
                <AISpeakingExecutionEvidence
                  review={review}
                  audio={data.audio}
                />
              ) : (
                <p className={css.notice}>
                  This historical assessment retains its configured provenance.
                  This screen does not create an AI proposal or infer
                  transcription readiness.
                </p>
              )}
              {!current && (
                <p className={css.notice}>
                  Historical version. Its evidence is read-only. Select the
                  current version to inspect available actions.
                </p>
              )}
              {review.history && review.history.length > 0 && (
                <label className={css.form}>
                  Assessment version to inspect
                  <select
                    disabled={busy}
                    value={exactVersion}
                    onChange={(event) => setVersionId(event.target.value)}
                  >
                    {review.history.map((item) => (
                      <option key={item.id} value={item.id}>
                        Version {item.version_number} · {item.status}
                        {item.id === review.current_version_id
                          ? " · current"
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <section className={css.card}>
                <h2>{review.task_label ?? "Exact Speaking task"}</h2>
                <p>
                  {review.task_prompt_available === false
                    ? "Question Bank read permission is required to inspect this task prompt."
                    : taskPrompt ||
                      "The task prompt is unavailable in this historical projection."}
                </p>
              </section>
              <AudioEvidenceView
                item={data.audio}
                organisationId={organisationId}
                assessmentContext={Boolean(human || executedAI)}
              />
              <TranscriptionEvidencePanel
                item={data.audio}
                organisationId={organisationId}
              />
              <dl className={css.provenance}>
                <dt>Assessment</dt>
                <dd>{data.assessmentId}</dd>
                <dt>Exact assessment version</dt>
                <dd>{exactVersion}</dd>
                <dt>Version checksum</dt>
                <dd>{review.checksum ?? "Unavailable"}</dd>
                <dt>Submitted response revision</dt>
                <dd>
                  {review.candidate_response_revision_id ??
                    data.audio.candidate_response_revision_id ??
                    "Unavailable"}
                </dd>
                <dt>Response checksum</dt>
                <dd>{review.response_checksum ?? "Unavailable"}</dd>
                <dt>Standard Pack version</dt>
                <dd>{review.standard_pack_version_id ?? "Unavailable"}</dd>
                <dt>Human assessor</dt>
                <dd>{review.assessor_id ?? "Not submitted"}</dd>
                <dt>Human assessment submitted</dt>
                <dd>
                  {String(review.provenance?.assessed_at ?? "Not submitted")}
                </dd>
                <dt>Decision reviewer</dt>
                <dd>{review.reviewer_id ?? "Not decided"}</dd>
                <dt>Decision recorded</dt>
                <dd>
                  {String(review.provenance?.reviewed_at ?? "Not decided")}
                </dd>
                <dt>Independent decision</dt>
                <dd>{review.review?.decision ?? "Not decided"}</dd>
                <dt>Private decision reason</dt>
                <dd>
                  {review.review?.reason ?? "No decision reason recorded"}
                </dd>
                <dt>Predecessor assessment version</dt>
                <dd>
                  {String(
                    review.provenance?.source_version_id ??
                      "Initial assessment version",
                  )}
                </dd>
                <dt>Review checksum</dt>
                <dd>{review.review?.reviewed_checksum ?? "Not requested"}</dd>
              </dl>
              {review.rubric && <RubricEvidenceView rubric={review.rubric} />}
              {canPropose && review.rubric && (
                <form
                  className={css.form}
                  onSubmit={(event) => void propose(event)}
                >
                  <h2>Enter human audio criterion judgments</h2>
                  <p>
                    Listen to the exact original recording. Give a bounded
                    score, a published performance level, private rationale and
                    original-audio time spans for every required criterion.
                  </p>
                  <HumanAudioCriterionFields
                    criteria={review.rubric.criteria}
                    levels={review.rubric.performance_levels}
                    durationMs={data.audio.duration_ms}
                    values={values}
                    disabled={busy}
                    onChange={(code, value) =>
                      setValues((previous) => ({ ...previous, [code]: value }))
                    }
                  />
                  <button disabled={busy}>
                    Submit human Speaking assessment for independent review
                  </button>
                </form>
              )}
              {review.criteria.length > 0 && (
                <section
                  className={css.card}
                  aria-label="Recorded Speaking criterion judgments"
                >
                  <h2>
                    {human
                      ? "Recorded human criterion judgments"
                      : "Existing recorded criterion proposals"}
                  </h2>
                  {review.criteria.map((criterion) => (
                    <article key={String(criterion.criterion_code)}>
                      <h3>{String(criterion.criterion_code)}</h3>
                      <p>
                        Submitted level:{" "}
                        {String(criterion.proposed_level ?? "Not recorded")} ·
                        Points:{" "}
                        {String(criterion.proposed_points ?? "Not recorded")}
                      </p>
                      <p>
                        Private rationale:{" "}
                        {String(criterion.rationale ?? "Not recorded")}
                      </p>
                      <details>
                        <summary>
                          Exact original audio evidence references
                        </summary>
                        <pre className={css.evidence}>
                          {JSON.stringify(criterion.evidence ?? [], null, 2)}
                        </pre>
                      </details>
                      <p>
                        Approved level:{" "}
                        {String(criterion.approved_level ?? "Not approved")} ·
                        Points:{" "}
                        {String(criterion.approved_points ?? "Not approved")}
                      </p>
                    </article>
                  ))}
                </section>
              )}
              {human &&
                review.status === "IN_REVIEW" &&
                review.assessor_id === user?.id && (
                  <p className={css.notice}>
                    Your judgments await an independent reviewer. You cannot
                    approve or reject your own submitted assessment.
                  </p>
                )}
              {executedAI && user && (
                <AISpeakingReviewControls
                  key={`${context}:${exactVersion}:${review.checksum}`}
                  review={review}
                  audio={data.audio}
                  organisationId={organisationId}
                  permissions={permissions}
                  userId={user.id}
                  onPersist={() => setRefresh((value) => value + 1)}
                />
              )}
              {canDecide && (
                <section className={css.card}>
                  <h2>Independent human review decision</h2>
                  <p>
                    Inspect the original recording, exact rubric and submitted
                    judgments before deciding this pinned version. The server
                    enforces actor separation and checksum freshness.
                  </p>
                  <label className={css.form}>
                    Decision reason for rejection or changes
                    <textarea
                      rows={3}
                      maxLength={2000}
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </label>
                  <div className={css.actions}>
                    <button
                      disabled={busy}
                      onClick={() => void decide("APPROVED")}
                    >
                      Approve human Speaking assessment
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void decide("CHANGES_REQUESTED")}
                    >
                      Request Speaking assessment changes
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void decide("REJECTED")}
                    >
                      Reject Speaking assessment
                    </button>
                  </div>
                </section>
              )}
              {review.status === "APPROVED" && (
                <p className={css.notice}>
                  Approved subjective score:{" "}
                  {review.approved_score ?? "See criterion evidence"}. This
                  exact version and its audio judgments are immutable. Result
                  scoring, human result approval and release are separate
                  governed steps.{" "}
                  {permissions.includes("exam_assignment.read") && (
                    <Link
                      href={`/institution/overview#attempt-${encodeURIComponent(review.attempt_id)}`}
                    >
                      Inspect submitted attempt and scoring →
                    </Link>
                  )}
                </p>
              )}
              {canSucceed && (
                <form
                  className={css.form}
                  onSubmit={(event) => void successor(event)}
                >
                  <h2>Governed reassessment</h2>
                  <p>
                    A successor preserves the approved evidence and requires new
                    criterion judgments and an independent decision.
                  </p>
                  <label>
                    Private reassessment reason
                    <textarea
                      required
                      rows={3}
                      maxLength={2000}
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </label>
                  <button disabled={busy || !reason.trim()}>
                    Create human Speaking successor version
                  </button>
                </form>
              )}
              {review.history && (
                <details className={css.card}>
                  <summary>Immutable assessment version history</summary>
                  <ul>
                    {review.history.map((item) => (
                      <li key={item.id}>
                        Version {item.version_number}: {item.status} · {item.id}{" "}
                        · {item.checksum}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
