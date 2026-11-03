"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  releaseApi,
  releaseStateMessage,
  type ReleaseArtifact,
  type ReleaseDetail,
} from "../../../../institution/releases/release-api";

export default function CandidateReleasedResultPage({
  params,
}: {
  params: Promise<{ releaseId: string }>;
}) {
  const [release, setRelease] = useState<ReleaseDetail | null>(null);
  const [statement, setStatement] = useState<ReleaseArtifact | null>(null);
  const [message, setMessage] = useState("Loading your released result…");
  const [error, setError] = useState<string | null>(null);
  const [statementMessage, setStatementMessage] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setRelease(null);
    setStatement(null);
    setError(null);
    setStatementMessage("");
    setMessage("Loading your released result…");
    void params
      .then(async ({ releaseId }) => {
        const item = await releaseApi.candidateDetail(releaseId);
        if (cancelled) return;
        setRelease(item);
        setMessage("");
        try {
          const artifact = await releaseApi.candidateStatement(releaseId);
          if (!cancelled) setStatement(artifact);
        } catch {
          if (!cancelled)
            setStatementMessage(
              "A Statement of Results is not available for this release. Contact your institution if you need assistance.",
            );
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
  }, [params, reload]);
  const effective = release?.effective_status ?? release?.status;
  return (
    <section>
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
      {release && (
        <>
          <p>
            <strong>Recorded status:</strong> {release.status}
          </p>
          <p>
            <strong>Effective status:</strong> {effective}
          </p>
          <p>{releaseStateMessage(effective ?? "")}</p>
          <p>Classification: {release.classification}</p>
          <p>Released: {new Date(release.released_at).toLocaleString()}</p>
          <p>
            Result version: <code>{release.result_version_id}</code>
          </p>
          <h2>Statement of Results</h2>
          {statementMessage && <p role="status">{statementMessage}</p>}
          {statement && (
            <article>
              <p>Statement: {statement.id}</p>
              <p>Recorded status: {statement.status ?? "Recorded"}</p>
              <p>Effective status: {statement.effective_status ?? effective}</p>
              {effective !== "RELEASED" && (
                <p>This is historical evidence and is not currently valid.</p>
              )}
              <dl>
                {Object.entries(statement.payload ?? {}).map(
                  ([name, value]) => (
                    <div key={name}>
                      <dt>{name.replaceAll("_", " ")}</dt>
                      <dd style={{ overflowWrap: "anywhere" }}>
                        {typeof value === "object"
                          ? JSON.stringify(value)
                          : String(value)}
                      </dd>
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
        </>
      )}
    </section>
  );
}
