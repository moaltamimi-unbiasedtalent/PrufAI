"use client";

import Link from "next/link";
import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../../../../lib/auth";
import {
  rubricActions,
  writingFoundationApi,
  type RubricVersionDetail,
} from "../../../../writing-reviews/foundation-api";
import {
  GovernanceError,
  RubricEvidenceView,
} from "../../../../writing-reviews/governance-ui";
import css from "../../../../assessment.module.css";

export default function InstitutionRubricVersionPage({
  params,
}: {
  params: Promise<{ rubricId: string; versionId: string }>;
}) {
  const { organisationId, permissions, user } = useAuth();
  const [rubric, setRubric] = useState<RubricVersionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const operations = useRef<Record<string, string>>({});
  const allowed = permissions.includes("rubric.read");
  useEffect(() => {
    let cancelled = false;
    setRubric(null);
    setError(null);
    if (!organisationId || !allowed) return;
    void params
      .then(({ rubricId, versionId }) =>
        writingFoundationApi.rubric(organisationId, rubricId, versionId),
      )
      .then((value) => {
        if (!cancelled) setRubric(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "This rubric version is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [organisationId, allowed, params, reload]);
  async function act(action: "request-review" | "approve" | "publish") {
    if (!organisationId || !rubric || !user || busy) return;
    const actions = rubricActions(rubric, permissions, user.id);
    if (
      (action === "request-review" && !actions.requestReview) ||
      (action === "approve" && !actions.approve) ||
      (action === "publish" && !actions.publish)
    )
      return;
    setBusy(true);
    setError(null);
    const key = `${rubric.id}:${action}:${rubric.checksum}`;
    const operationId = (operations.current[key] ??= crypto.randomUUID());
    try {
      await writingFoundationApi.rubricAction(
        organisationId,
        rubric.rubric_id,
        rubric.id,
        action,
        rubric.checksum,
        operationId,
      );
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The governed action could not be confirmed. Refresh before continuing.",
      );
    } finally {
      setBusy(false);
    }
  }
  const actions =
    rubric && user ? rubricActions(rubric, permissions, user.id) : null;
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/rubrics">← Institution rubrics</Link>
      </p>
      <h1>Rubric version governance</h1>
      {!allowed || !organisationId ? (
        <p role="alert">
          Rubric read permission in the selected institution is required.
        </p>
      ) : (
        <>
          {error && (
            <GovernanceError
              error={error}
              retry={() => setReload((value) => value + 1)}
            />
          )}
          {!rubric && !error && (
            <p role="status">Loading exact rubric version…</p>
          )}
          {rubric && (
            <>
              <h2>{rubric.name ?? rubric.version_label}</h2>
              <p>Status: {rubric.status}</p>
              <p>
                Skill: {rubric.skill} · Language:{" "}
                {rubric.language ?? "Not specified"}
              </p>
              <details className={css.card}>
                <summary>Published assessment and human review policy</summary>
                <pre className={css.evidence}>
                  {JSON.stringify(
                    {
                      assessment_policy: rubric.ai_assessment_policy,
                      human_review_policy: rubric.human_review_policy,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <dl className={css.provenance}>
                <dt>Standard Pack version</dt>
                <dd>{rubric.standard_pack_version_id}</dd>
                <dt>Exact task specification</dt>
                <dd>{rubric.task_spec_id ?? "Not bound"}</dd>
                <dt>Review requester</dt>
                <dd>{rubric.review?.requested_by ?? "Not requested"}</dd>
                <dt>Review state</dt>
                <dd>{rubric.review?.status ?? "Not requested"}</dd>
                <dt>Review requested</dt>
                <dd>{rubric.review?.requested_at ?? "Not requested"}</dd>
                <dt>Approved by</dt>
                <dd>
                  {rubric.approved_by_user_id ??
                    rubric.review?.decided_by ??
                    "Not approved"}
                </dd>
                <dt>Published by</dt>
                <dd>{rubric.published_by_user_id ?? "Not published"}</dd>
                <dt>Published at</dt>
                <dd>{rubric.published_at ?? "Not published"}</dd>
                <dt>Approved at</dt>
                <dd>{rubric.approved_at ?? "Not approved"}</dd>
              </dl>
              <RubricEvidenceView rubric={rubric} />
              <div className={css.actions}>
                {actions?.requestReview && (
                  <button
                    disabled={busy}
                    onClick={() => void act("request-review")}
                  >
                    Request rubric review
                  </button>
                )}
                {actions?.approve && (
                  <button disabled={busy} onClick={() => void act("approve")}>
                    Approve reviewed rubric
                  </button>
                )}
                {actions?.publish && (
                  <button disabled={busy} onClick={() => void act("publish")}>
                    Publish approved rubric
                  </button>
                )}
              </div>
              {rubric.status === "HUMAN_REVIEW" &&
                rubric.review?.requested_by === user?.id && (
                  <p className={css.notice}>
                    An independent authorised reviewer must approve this rubric.
                    The review requester cannot approve their own request.
                  </p>
                )}
              {["PUBLISHED", "ACTIVE"].includes(rubric.status) && (
                <p className={css.notice}>
                  This exact rubric version is published and eligible for
                  compatible {rubric.skill} review. Its criteria and policies
                  are immutable.
                </p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
