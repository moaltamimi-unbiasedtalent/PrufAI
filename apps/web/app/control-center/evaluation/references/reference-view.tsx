"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import assessmentCss from "../../../institution/assessment.module.css";
import {
  HumanCriterionFields,
  RubricEvidenceView,
  type HumanCriterionEntry,
} from "../../../institution/writing-reviews/governance-ui";
import css from "../../control-center.module.css";
import {
  referenceApi,
  type ReferenceCriterion,
  type ReferenceWorkbench,
} from "./reference-api";

export function referenceCriteria(
  values: Record<string, HumanCriterionEntry>,
  text: string,
): ReferenceCriterion[] {
  return Object.entries(values).map(([criterion_code, value]) => {
    const position = text.indexOf(value.evidence);
    if (!value.evidence || position < 0)
      throw new Error(
        `An exact source quote is required for ${criterion_code}.`,
      );
    const start = Array.from(text.slice(0, position)).length;
    return {
      criterion_code,
      points: Number(value.points),
      level: value.level,
      rationale: value.reason,
      evidence: [
        {
          quote: value.evidence,
          start,
          end: start + Array.from(value.evidence).length,
        },
      ],
    };
  });
}

export function PrivateReferenceEvidence({
  workbench,
}: {
  workbench: ReferenceWorkbench;
}) {
  const source = workbench.source;
  if (!source)
    return (
      <p className={css.notice}>
        Private evidence is unavailable to this identity. Reference metadata
        does not grant evidence access.
      </p>
    );
  return (
    <section
      className={css.card}
      aria-label="Authorized private reference evidence"
    >
      <h2>Exact private source evidence</h2>
      <p>
        Evidence class: {workbench.reference.evidence_class}.{" "}
        {workbench.reference.evidence_class === "SYNTHETIC_TEST"
          ? "Synthetic technical reference; no genuine examiner qualification or language calibration is claimed."
          : "Restricted authorized evidence."}
      </p>
      <dl className={css.health}>
        <dt>Origin</dt>
        <dd>{source.origin}</dd>
        <dt>Language</dt>
        <dd>{source.language}</dd>
        <dt>Allowed purpose</dt>
        <dd>{source.purpose}</dd>
        <dt>Retention</dt>
        <dd>{source.retention_classification}</dd>
        <dt>Standard Pack version</dt>
        <dd>{source.standard_pack_version_id}</dd>
        <dt>Standard Pack checksum</dt>
        <dd>{source.standard_pack_checksum}</dd>
        <dt>Rubric version</dt>
        <dd>{source.rubric_version_id ?? "Not applicable"}</dd>
        <dt>Rubric checksum</dt>
        <dd>{source.rubric_checksum ?? "Not applicable"}</dd>
      </dl>
      {source.input.text ? (
        <>
          <h3>Exact source text</h3>
          <blockquote style={{ whiteSpace: "pre-wrap" }}>
            {source.input.text}
          </blockquote>
        </>
      ) : null}
      {source.input.audio_sha256 ? (
        <p>
          Original audio checksum: {source.input.audio_sha256}. Duration:{" "}
          {source.input.duration_ms} ms. Audio playback is not exposed by this
          workbench.
        </p>
      ) : null}
      {workbench.rubric &&
      source.rubric_version_id &&
      source.rubric_checksum ? (
        <RubricEvidenceView
          rubric={{
            ...workbench.rubric,
            id: source.rubric_version_id,
            checksum: source.rubric_checksum,
            aggregation_policy: workbench.rubric.aggregation_policy ?? {
              status: "NOT_AVAILABLE",
            },
            evidence_policy: workbench.rubric.evidence_policy ?? {
              status: "NOT_AVAILABLE",
            },
          }}
        />
      ) : null}
    </section>
  );
}

export function ReferenceAction({
  workbench,
  action,
  children,
}: {
  workbench: ReferenceWorkbench;
  action: string;
  children: React.ReactNode;
}) {
  return workbench.allowed_actions.includes(action) ? <>{children}</> : null;
}

export function VisibleReferenceJudgments({
  workbench,
  examinerId,
}: {
  workbench: ReferenceWorkbench;
  examinerId: string;
}) {
  const visible =
    workbench.reference.judgment_count <
    workbench.reference.required_judgment_count
      ? workbench.judgments.filter(
          (judgment) => judgment.examiner_id === examinerId,
        )
      : workbench.judgments;
  return (
    <section aria-label="Immutable submitted judgment receipts">
      <h2>Visible immutable judgment receipts</h2>
      {visible.length ? (
        visible.map((judgment) => (
          <article className={css.card} key={judgment.id}>
            <h3>Judgment {judgment.id}</h3>
            <p>
              {judgment.checksum} · examiner {judgment.examiner_id}
            </p>
            {judgment.body.criteria.map((criterion) => (
              <dl key={criterion.criterion_code}>
                <dt>
                  {criterion.criterion_code} · immutable submitted judgment
                </dt>
                <dd>
                  {criterion.points} points · {criterion.level}
                </dd>
                <dt>Private examiner rationale</dt>
                <dd>{criterion.rationale}</dd>
                {criterion.evidence.map((evidence, index) => (
                  <React.Fragment key={index}>
                    <dt>
                      Exact evidence [{evidence.start}, {evidence.end})
                    </dt>
                    <dd>{evidence.quote}</dd>
                  </React.Fragment>
                ))}
              </dl>
            ))}
          </article>
        ))
      ) : (
        <p>
          No judgment receipt is visible to this identity. Other independent
          submissions remain blinded.
        </p>
      )}
    </section>
  );
}

export default function ReferenceWorkbenchView() {
  const { user } = useAuth();
  const allowed = Boolean(
    user?.platform_permissions.includes("standards.evaluation.read") &&
      user.platform_permissions.some((permission) =>
        ["ai.manage", "ai.review"].includes(permission),
      ),
  );
  const [id, setId] = useState("");
  const [selected, setSelected] = useState("");
  const [reload, setReload] = useState(0);
  const [workbench, setWorkbench] = useState<ReferenceWorkbench | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [qualification, setQualification] = useState("");
  const [values, setValues] = useState<Record<string, HumanCriterionEntry>>({});
  const [reviewReference, setReviewReference] = useState("");
  const [rationale, setRationale] = useState("");
  const [operation, setOperation] = useState("");
  useEffect(() => {
    const reference = new URLSearchParams(window.location.search).get(
      "reference",
    );
    if (reference) {
      setId(reference);
      setSelected(reference);
    }
  }, []);
  useEffect(() => {
    let cancelled = false;
    setWorkbench(null);
    setError(null);
    setValues({});
    setQualification("");
    if (!allowed || !selected) return;
    void Promise.resolve()
      .then(() => referenceApi.workbench(selected))
      .then((value) => {
        if (!cancelled) {
          setWorkbench(value);
          setQualification(value.qualifications[0]?.id ?? "");
          setOperation(crypto.randomUUID());
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Private reference is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, selected, reload, user?.id]);

  async function act(
    action: "approve" | "judgments" | "adjudications" | "dataset-versions",
  ) {
    if (!workbench || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      let body: Record<string, unknown> = {
        expected_checksum: workbench.reference.checksum,
        review_reference: reviewReference,
      };
      if (action === "judgments" || action === "adjudications") {
        const credential = workbench.qualifications.find(
          (item) => item.id === qualification,
        );
        if (!credential)
          throw new Error("An approved exact qualification is required.");
        body = {
          ...body,
          operation_id: operation,
          qualification_id: credential.id,
          qualification_checksum: credential.checksum,
          criteria: referenceCriteria(
            values,
            workbench.source?.input.text ?? "",
          ),
          transcript: null,
          relevance: [],
        };
      }
      if (action === "adjudications")
        body = {
          ...body,
          judgment_pins: workbench.judgments.map((item) => ({
            id: item.id,
            checksum: item.checksum,
          })),
          rationale,
          supersedes_id: workbench.adjudications.at(-1)?.id ?? null,
        };
      if (action === "dataset-versions") {
        const adjudication = workbench.adjudications.at(-1);
        if (!adjudication)
          throw new Error(
            "An exact adjudication is required before deriving a dataset.",
          );
        body = {
          operation_id: operation,
          expected_checksum: workbench.reference.checksum,
          adjudication_id: adjudication.id,
          adjudication_checksum: adjudication.checksum,
        };
      }
      const receipt = await referenceApi.act(selected, action, body);
      setResult(
        `Recorded immutable ${action} receipt: ${receipt.id}. Checksum: ${receipt.checksum}.`,
      );
      setReload((value) => value + 1);
    } catch (cause: unknown) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The operation could not be recorded.",
      );
    } finally {
      setBusy(false);
    }
  }

  const judgmentAllowed =
    workbench?.allowed_actions.includes("SUBMIT_JUDGMENT");
  const adjudicationAllowed = workbench?.allowed_actions.includes("ADJUDICATE");
  return (
    <>
      <p className={css.eyebrow}>Restricted evaluation governance</p>
      <h1>Private reference workbench</h1>
      <Link href="/control-center/evaluation">
        Return to safe Evaluation metadata
      </Link>
      <p className={css.notice}>
        Submitted judgments and adjudications are immutable. Independent
        judgments remain blinded until authorized disclosure. Technical test
        actors and labels remain SYNTHETIC_TEST. Empirical thresholds are
        UNCONFIGURED; production activation is not authorized.
      </p>
      {!allowed ? (
        <p className={css.error} role="alert">
          Your current platform permissions do not allow this restricted
          workbench.
        </p>
      ) : (
        <>
          <form
            className={assessmentCss.form}
            onSubmit={(event) => {
              event.preventDefault();
              setSelected(id.trim());
              setReload((value) => value + 1);
              setResult(null);
            }}
          >
            <label>
              Exact reference ID{" "}
              <input
                required
                value={id}
                onChange={(event) => setId(event.target.value)}
              />
            </label>{" "}
            <button className={css.button} disabled={busy}>
              Load exact reference
            </button>
          </form>
          {error ? (
            <p className={css.error} role="alert">
              {error}
            </p>
          ) : null}
          {result ? (
            <p className={css.notice} role="status">
              {result}
            </p>
          ) : null}
          {selected && !workbench && !error ? (
            <p role="status">Loading authorized private reference…</p>
          ) : null}
          {workbench ? (
            <>
              <h2>Reference {workbench.reference.id}</h2>
              <p>Exact checksum: {workbench.reference.checksum}</p>
              <p>
                Approval: {workbench.reference.status}. Judgments:{" "}
                {workbench.reference.judgment_count} /{" "}
                {workbench.reference.required_judgment_count}. Adjudication:{" "}
                {workbench.reference.adjudication_status}. Split:{" "}
                {workbench.reference.split}.
              </p>
              <PrivateReferenceEvidence workbench={workbench} />
              <VisibleReferenceJudgments
                workbench={workbench}
                examinerId={user?.id ?? ""}
              />
              {workbench.adjudications.length ? (
                <section>
                  <h2>Immutable adjudication receipts</h2>
                  <ul>
                    {workbench.adjudications.map((item) => (
                      <li key={item.id}>
                        Version {item.version_number}: {item.id} ·{" "}
                        {item.checksum}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
              <ReferenceAction workbench={workbench} action="APPROVE_SOURCE">
                <form
                  className={assessmentCss.form}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act("approve");
                  }}
                >
                  <label>
                    Source approval review reference{" "}
                    <input
                      required
                      value={reviewReference}
                      onChange={(event) =>
                        setReviewReference(event.target.value)
                      }
                    />
                  </label>{" "}
                  <button className={css.button} disabled={busy}>
                    Approve exact source provenance
                  </button>
                </form>
              </ReferenceAction>
              {(judgmentAllowed || adjudicationAllowed) &&
              workbench.rubric &&
              workbench.source?.input.text ? (
                <form
                  className={assessmentCss.form}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act(
                      adjudicationAllowed ? "adjudications" : "judgments",
                    );
                  }}
                >
                  <h2>
                    {adjudicationAllowed
                      ? "Independent adjudication"
                      : "Independent examiner judgment"}
                  </h2>
                  {adjudicationAllowed && workbench.adjudications.length ? (
                    <p>
                      New immutable successor to exact adjudication{" "}
                      {workbench.adjudications.at(-1)?.id}. Prior submitted
                      judgments and adjudications remain unchanged.
                    </p>
                  ) : null}
                  <label>
                    Approved qualification{" "}
                    <select
                      required
                      value={qualification}
                      onChange={(event) => setQualification(event.target.value)}
                    >
                      {!qualification ? (
                        <option value="">
                          No approved qualification available
                        </option>
                      ) : null}
                      {workbench.qualifications.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.id} · {item.checksum}
                        </option>
                      ))}
                    </select>
                  </label>
                  <HumanCriterionFields
                    criteria={workbench.rubric.criteria}
                    levels={workbench.rubric.performance_levels}
                    values={values}
                    disabled={busy}
                    evidenceRequired
                    onChange={(code, value) =>
                      setValues((current) => ({ ...current, [code]: value }))
                    }
                  />
                  <label>
                    Judgment review operation reference{" "}
                    <input
                      required
                      value={reviewReference}
                      onChange={(event) =>
                        setReviewReference(event.target.value)
                      }
                    />
                  </label>
                  {adjudicationAllowed ? (
                    <label>
                      Private adjudication rationale{" "}
                      <textarea
                        required
                        maxLength={2000}
                        value={rationale}
                        onChange={(event) => setRationale(event.target.value)}
                      />
                    </label>
                  ) : null}
                  <button className={css.button} disabled={busy}>
                    {adjudicationAllowed
                      ? "Submit immutable adjudication"
                      : "Submit immutable independent judgment"}
                  </button>
                </form>
              ) : null}
              <ReferenceAction workbench={workbench} action="DERIVE_DATASET">
                <button
                  className={css.secondary}
                  disabled={busy}
                  onClick={() => void act("dataset-versions")}
                >
                  Derive draft dataset from exact adjudication
                </button>
              </ReferenceAction>
              {!workbench.allowed_actions.length ? (
                <p className={css.notice}>
                  No mutation is authorized for this identity and exact
                  reference state.
                </p>
              ) : null}
            </>
          ) : null}
        </>
      )}
    </>
  );
}
