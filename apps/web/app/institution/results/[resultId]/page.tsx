"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import {
  assessmentApi,
  resultActions,
  type ResultVersion,
  type ScoringRun,
} from "../../assessment-api";
import { releaseApi, type ResultRelease } from "../../releases/release-api";
import css from "../../assessment.module.css";
import { AssessmentEvidence } from "../../assessment-evidence";

export default function ResultReviewPage({
  params,
}: {
  params: Promise<{ resultId: string }>;
}) {
  const { organisationId, permissions } = useAuth();
  const router = useRouter();
  const [versions, setVersions] = useState<ResultVersion[] | null>(null);
  const [releases, setReleases] = useState<ResultRelease[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [run, setRun] = useState<ScoringRun | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const operations = useRef<Record<string, string>>({});
  const allowed = permissions.includes("result.read");
  useEffect(() => {
    let cancelled = false;
    setVersions(null);
    setError(null);
    if (!organisationId || !allowed) return;
    void params
      .then(async ({ resultId }) => {
        const [items, history] = await Promise.all([
          assessmentApi.versions(organisationId, resultId),
          releaseApi.list(organisationId),
        ]);
        if (cancelled) return;
        const sorted = [...items].sort(
          (a, b) => b.version_number - a.version_number,
        );
        setVersions(sorted);
        setReleases(
          history.filter(
            (release) => release.assessment_result_id === resultId,
          ),
        );
        setSelectedId((current) =>
          sorted.some((version) => version.result_version_id === current)
            ? current
            : (sorted[0]?.result_version_id ?? ""),
        );
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Result history could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, organisationId, params, reload]);
  const version = versions?.find(
    (item) => item.result_version_id === selectedId,
  );
  const latest = Boolean(
    version && versions?.[0]?.result_version_id === version.result_version_id,
  );
  useEffect(() => {
    let cancelled = false;
    setRun(null);
    setRunError(null);
    if (!organisationId || !version?.scoring_run_id || !allowed) return;
    void assessmentApi
      .scoringRun(organisationId, version.scoring_run_id)
      .then((value) => {
        if (!cancelled) setRun(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setRunError(
            cause instanceof Error
              ? cause.message
              : "The exact scoring run is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, organisationId, version?.scoring_run_id, reload]);
  async function act(action: "review" | "approve" | "release") {
    if (!organisationId || !version || busy) return;
    const available = resultActions(version, latest, permissions);
    if (
      (action === "review" && !available.requestReview) ||
      (action === "approve" && !available.approve) ||
      (action === "release" && !available.release)
    )
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (action === "review") {
        await assessmentApi.requestReview(
          organisationId,
          version.result_version_id,
        );
        setNotice("Human result review requested for this exact checksum.");
      } else if (action === "approve") {
        await assessmentApi.approve(organisationId, version.result_version_id);
        setNotice(
          "Human result review approved. Release remains a separate authorized action.",
        );
      } else {
        const operation = (operations.current[version.result_version_id] ??=
          crypto.randomUUID());
        const result = await releaseApi.release(
          organisationId,
          version.result_version_id,
          operation,
        );
        router.push(`/institution/releases/${result.release_id}`);
        return;
      }
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The result action could not be confirmed. Refresh before continuing.",
      );
    } finally {
      setBusy(false);
    }
  }
  const actions = version ? resultActions(version, latest, permissions) : null;
  const recorded = releases.find(
    (release) => release.result_version_id === version?.result_version_id,
  );
  return (
    <section>
      <p>
        <Link href="/institution/overview">← Institution inspection</Link>
      </p>
      <h1>Result review and release</h1>
      <p>
        Inspect the persisted deterministic score and exact version before human
        approval. Release and Statement of Results issuance are separate
        governed actions.
      </p>
      {!allowed || !organisationId ? (
        <p className={css.error} role="alert">
          Result read permission in the selected institution is required.
        </p>
      ) : (
        <>
          {error && (
            <div className={css.error} role="alert">
              <p>{error}</p>
              <button onClick={() => setReload((value) => value + 1)}>
                Refresh result
              </button>
            </div>
          )}
          {notice && (
            <p className={css.notice} role="status">
              {notice}
            </p>
          )}
          {!versions && !error && (
            <p role="status">Loading persisted result versions…</p>
          )}
          {versions && !versions.length && (
            <p>No result versions are available.</p>
          )}
          {version && (
            <>
              <label className={css.form}>
                Result version
                <select
                  value={selectedId}
                  onChange={(event) => setSelectedId(event.target.value)}
                >
                  {versions?.map((item) => (
                    <option
                      key={item.result_version_id}
                      value={item.result_version_id}
                    >
                      Version {item.version_number} · {item.status}
                    </option>
                  ))}
                </select>
              </label>
              <section className={css.card}>
                <h2>Persisted result</h2>
                <p>
                  <strong>Raw score:</strong>{" "}
                  {version.raw_awarded_points ?? "Pending"} /{" "}
                  {version.raw_maximum_points ?? "Unavailable"}
                </p>
                <p>
                  <strong>Outcome:</strong>{" "}
                  {version.outcome ?? "Not determined"}
                </p>
                <p>
                  <strong>Result status:</strong> {version.status}
                </p>
                {version.incomplete_reason && (
                  <p>Incomplete reason: {version.incomplete_reason}</p>
                )}
                <dl className={css.provenance}>
                  <dt>Assessment result</dt>
                  <dd>{version.result_id}</dd>
                  <dt>Result version</dt>
                  <dd>{version.result_version_id}</dd>
                  <dt>Attempt</dt>
                  <dd>{version.attempt_id}</dd>
                  <dt>Result checksum</dt>
                  <dd>{version.checksum}</dd>
                  <dt>Response-set checksum</dt>
                  <dd>{version.response_set_checksum}</dd>
                  <dt>Scoring-run checksum</dt>
                  <dd>{version.scoring_run_checksum}</dd>
                  <dt>Human review</dt>
                  <dd>{version.review?.status ?? "Not requested"}</dd>
                </dl>
              </section>
              <section className={css.card}>
                <h2>Exact ScoringRun</h2>
                {runError ? (
                  <p role="alert">{runError}</p>
                ) : !run ? (
                  <p role="status">Loading pinned scoring run…</p>
                ) : (
                  <dl className={css.provenance}>
                    <dt>Run reference</dt>
                    <dd>{run.id}</dd>
                    <dt>State</dt>
                    <dd>{run.status}</dd>
                    <dt>Engine</dt>
                    <dd>{run.scoring_engine}</dd>
                    <dt>Checksum</dt>
                    <dd>{run.checksum}</dd>
                    <dt>Response-set checksum</dt>
                    <dd>{run.response_set_checksum}</dd>
                  </dl>
                )}
                <button onClick={() => setReload((value) => value + 1)}>
                  Refresh evidence
                </button>
              </section>
              <AssessmentEvidence components={version.assessment_components} />
              <details className={css.card}>
                <summary>Governed rules and scoring provenance</summary>
                <pre className={css.evidence}>
                  {JSON.stringify(version.rule_provenance, null, 2)}
                </pre>
              </details>
              {!latest && (
                <p className={css.notice}>
                  Historical version. New review and release actions are
                  unavailable for this version.
                </p>
              )}
              <div className={css.actions}>
                {actions?.requestReview && (
                  <button disabled={busy} onClick={() => void act("review")}>
                    Request human result review
                  </button>
                )}
                {actions?.approve && (
                  <button disabled={busy} onClick={() => void act("approve")}>
                    Approve reviewed result
                  </button>
                )}
                {actions?.release && !recorded && !releases.length && (
                  <button disabled={busy} onClick={() => void act("release")}>
                    Release approved result
                  </button>
                )}
                {actions?.release && !recorded && releases.length > 0 && (
                  <Link href="/institution/releases">
                    Continue governed successor release with exact predecessor →
                  </Link>
                )}
                {recorded && (
                  <Link href={`/institution/releases/${recorded.id}`}>
                    Inspect release and issue Statement of Results →
                  </Link>
                )}
              </div>
              <p>
                The server checks the current review checksum, lifecycle and
                permission for every action. An unapproved or stale result
                cannot be released.
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
