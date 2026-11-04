"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "../../../../../lib/auth";
import {
  AssessmentEvidence,
  assessmentSkillName,
} from "../../../../institution/assessment-evidence";
import {
  releaseApi,
  releaseStateMessage,
  exactReleasedScore,
  ReleaseApiError,
  statementMatchesRelease,
  statementProvenanceFields,
  type ReleaseArtifact,
  type ReleaseDetail,
} from "../../../../institution/releases/release-api";

export default function CandidateReleasedResultPage({
  params,
}: {
  params: Promise<{ releaseId: string }>;
}) {
  const { organisationId, user } = useAuth();
  const context =
    organisationId && user ? `${organisationId}:${user.id}` : null;
  const [loadedContext, setLoadedContext] = useState<string | null>(null);
  const [release, setRelease] = useState<ReleaseDetail | null>(null);
  const [statement, setStatement] = useState<ReleaseArtifact | null>(null);
  const [message, setMessage] = useState("Loading your released result…");
  const [error, setError] = useState<string | null>(null);
  const [statementMessage, setStatementMessage] = useState("");
  const [statementError, setStatementError] = useState<string | null>(null);
  const [statementLoading, setStatementLoading] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setRelease(null);
    setLoadedContext(null);
    setStatement(null);
    setError(null);
    setStatementMessage("");
    setStatementError(null);
    setStatementLoading(false);
    if (!context) {
      setMessage(
        "Sign in with an active candidate membership to inspect your released result.",
      );
      return;
    }
    setMessage("Loading your released result…");
    void params
      .then(async ({ releaseId }) => {
        const item = await releaseApi.candidateDetail(releaseId);
        if (cancelled) return;
        setRelease(item);
        setLoadedContext(context);
        setMessage("");
        setStatementLoading(true);
        try {
          const artifact = await releaseApi.candidateStatement(releaseId);
          if (!cancelled) {
            if (!statementMatchesRelease(artifact, item)) {
              setStatementError(
                "The statement provenance does not match this exact release. Refresh or contact your institution.",
              );
            } else setStatement(artifact);
          }
        } catch (cause) {
          if (!cancelled) {
            if (cause instanceof ReleaseApiError && cause.status === 404)
              setStatementMessage(
                "A Statement of Results is not available for this release. Contact your institution if you need assistance.",
              );
            else
              setStatementError(
                cause instanceof Error
                  ? cause.message
                  : "The statement could not be loaded. Retry the connection.",
              );
          }
        } finally {
          if (!cancelled) setStatementLoading(false);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setMessage("");
          setError(
            cause instanceof Error
              ? cause.message
              : "Your result is unavailable.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [params, reload, context]);
  const effective = release?.effective_status ?? release?.status;
  const score =
    release && loadedContext === context ? exactReleasedScore(release) : null;
  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/candidate/results">← My released results</Link>
      </p>
      <h1>My released result</h1>
      {message && <p role="status">{message}</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Retry result
          </button>
        </div>
      )}
      {release && loadedContext === context && (
        <>
          <p>
            <strong>Recorded status:</strong> {release.status}
          </p>
          <p>
            <strong>Effective status:</strong> {effective}
          </p>
          <p>{releaseStateMessage(effective ?? "")}</p>
          <p>Classification: {release.classification}</p>
          {release.classification === "PRACTICE" && (
            <p>
              This is a practice result and Statement of Results, not an
              official provider certificate.
            </p>
          )}
          {score && (
            <section aria-label="Released score">
              <h2>Released score</h2>
              <p>
                Raw score: {score.raw_awarded_points ?? "Pending"} /{" "}
                {score.raw_maximum_points ?? "Unavailable"}
              </p>
              <p>Outcome: {score.outcome ?? "Not determined"}</p>
            </section>
          )}
          {!score && (
            <p>
              The score summary is unavailable for this exact release version.
            </p>
          )}
          <AssessmentEvidence components={score?.assessment_components} />
          <p>Released: {new Date(release.released_at).toLocaleString()}</p>
          <p>
            Result version: <code>{release.result_version_id}</code>
          </p>
          <h2>Statement of Results</h2>
          {statementLoading && (
            <p role="status">Loading your Statement of Results…</p>
          )}
          {statementMessage && <p role="status">{statementMessage}</p>}
          {statementError && <p role="alert">{statementError}</p>}
          {(statementMessage || statementError) && (
            <button onClick={() => setReload((value) => value + 1)}>
              Retry statement
            </button>
          )}
          {statement && (
            <article>
              <p>Statement: {statement.id}</p>
              <p>Recorded status: {statement.status ?? "Recorded"}</p>
              <p>Statement checksum: {statement.checksum ?? "Unavailable"}</p>
              <p>
                Statement issued:{" "}
                {statement.issued_at
                  ? new Date(statement.issued_at).toLocaleString()
                  : "Not available in this historical projection"}
              </p>
              {[
                ...new Set(
                  statement.payload?.assessment_components?.map(
                    (component) => component.skill,
                  ) ?? [],
                ),
              ].map((skill) => (
                <p key={skill}>Assessment: {assessmentSkillName(skill)}</p>
              ))}
              <p>
                Statement issuer reference:{" "}
                {String(
                  statement.payload?.issued_by_user_id ??
                    "Not recorded in this historical statement",
                )}
              </p>
              <p>Effective status: {statement.effective_status ?? effective}</p>
              {effective !== "RELEASED" && (
                <p>This is historical evidence and is not currently valid.</p>
              )}
              <dl>
                {statementProvenanceFields(statement).map(
                  ({ label, value }) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd style={{ overflowWrap: "anywhere" }}>{value}</dd>
                    </div>
                  ),
                )}
              </dl>
            </article>
          )}
          {effective === "RELEASED" && (
            <p>
              <Link
                href={`/candidate/results/appeal?resultVersionId=${encodeURIComponent(release.result_version_id)}`}
              >
                Appeal this result version
              </Link>
            </p>
          )}
          <p>Your institution manages result release and statement issuance.</p>
          <p>
            <Link href="/candidate/results/appeals">My appeal history →</Link>
          </p>
        </>
      )}
    </section>
  );
}
