"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { pilotActive } from "../../../../lib/access";
import { useAuth } from "../../../../lib/auth";
import {
  releaseActions,
  releaseApi,
  releaseStateMessage,
  type ReleaseArtifact,
  type ReleaseDetail,
} from "../release-api";

export default function ReleaseDetailPage({
  params,
}: {
  params: Promise<{ releaseId: string }>;
}) {
  const { organisationId, permissions, user } = useAuth();
  const [release, setRelease] = useState<ReleaseDetail | null>(null);
  const [message, setMessage] = useState("Loading release detail…");
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const operations = useRef<Record<string, string>>({});
  const canRead = permissions.includes("result.read");

  useEffect(() => {
    let cancelled = false;
    setRelease(null);
    setError(null);
    if (!organisationId || !canRead) {
      setMessage("Result inspection permission is required.");
      return;
    }
    setMessage("Loading release detail…");
    void params
      .then(({ releaseId }) => releaseApi.detail(organisationId, releaseId))
      .then((item) => {
        if (!cancelled) {
          setRelease(item);
          setMessage("");
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setMessage("");
          setError(
            cause instanceof Error
              ? cause.message
              : "Release detail could not be loaded.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [canRead, organisationId, params, reload]);

  async function issueStatement() {
    if (
      !organisationId ||
      !release ||
      busy ||
      Boolean(release.statements?.length) ||
      !releaseActions(release.effective_status ?? release.status, permissions)
        .issueStatement
    )
      return;
    setBusy(true);
    setError(null);
    const operation = (operations.current[`statement:${release.id}`] ??=
      crypto.randomUUID());
    try {
      await releaseApi.issueStatement(organisationId, release.id, operation);
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Statement issuance was blocked.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function revoke(event: FormEvent) {
    event.preventDefault();
    if (
      !organisationId ||
      !release ||
      !releaseActions(release.status, permissions).revoke
    )
      return;
    setBusy(true);
    setError(null);
    const key = `revoke:${release.id}:${reason.trim()}`;
    const operation = (operations.current[key] ??= crypto.randomUUID());
    try {
      await releaseApi.revoke(
        organisationId,
        release.id,
        reason.trim(),
        operation,
      );
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Revocation was blocked.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={{ overflowWrap: "anywhere" }}>
      <p>
        <Link href="/institution/releases">← Release queue and history</Link>
      </p>
      <h1>Result release detail</h1>
      {message && <p role="status">{message}</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Refresh release
          </button>
        </div>
      )}
      {release && (
        <>
          <p>
            <strong>Recorded status:</strong> {release.status}
          </p>
          <p>
            <strong>Effective status:</strong>{" "}
            {release.effective_status ?? release.status}
          </p>
          <p>
            {releaseStateMessage(release.effective_status ?? release.status)}
          </p>
          <dl>
            <dt>Release</dt>
            <dd>{release.id}</dd>
            <dt>Candidate</dt>
            <dd>{release.candidate_user_id}</dd>
            <dt>Classification</dt>
            <dd>{release.classification}</dd>
            <dt>Result version</dt>
            <dd>{release.result_version_id}</dd>
            <dt>Attempt</dt>
            <dd>{release.attempt_id ?? "Unavailable"}</dd>
            <dt>Scoring run</dt>
            <dd>{release.scoring_run_id ?? "Unavailable"}</dd>
            <dt>Standard Pack version</dt>
            <dd>{release.standard_pack_version_id ?? "Unavailable"}</dd>
            <dt>Release checksum</dt>
            <dd style={{ overflowWrap: "anywhere" }}>
              {release.checksum ?? "Unavailable"}
            </dd>
          </dl>
          {release.supersedes_release_id && (
            <p>
              Supersedes{" "}
              <Link
                href={`/institution/releases/${release.supersedes_release_id}`}
              >
                {release.supersedes_release_id}
              </Link>
            </p>
          )}
          <h2>Statements of Results</h2>
          {!release.statements?.length && <p>No statement has been issued.</p>}
          <ArtifactList
            artifacts={release.statements ?? []}
            releaseStatus={release.effective_status ?? release.status}
          />
          {!release.statements?.length &&
            releaseActions(
              release.effective_status ?? release.status,
              permissions,
            ).issueStatement && (
              <button disabled={busy} onClick={() => void issueStatement()}>
                Issue Statement of Results
              </button>
            )}
          {!pilotActive(user) && (
            <>
              <h2>Certificate records</h2>
              {!release.certificates?.length && (
                <p>
                  No certificate record is available. Certification requires
                  separate explicit scoped authority and remains disabled by
                  default.
                </p>
              )}
              <ArtifactList
                artifacts={release.certificates ?? []}
                releaseStatus={release.effective_status ?? release.status}
              />
            </>
          )}
          {releaseActions(release.status, permissions).revoke && (
            <form
              onSubmit={(event) => void revoke(event)}
              style={{ marginTop: "2rem" }}
            >
              <label>
                Revocation reason{" "}
                <input
                  required
                  maxLength={240}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <button disabled={busy || !reason.trim()}>Revoke release</button>
            </form>
          )}
        </>
      )}
    </section>
  );
}

function ArtifactList({
  artifacts,
  releaseStatus,
}: {
  artifacts: ReleaseArtifact[];
  releaseStatus: string;
}) {
  return (
    <ul>
      {artifacts.map((artifact) => (
        <li key={artifact.id}>
          <p>Artifact: {artifact.id}</p>
          <p>Recorded status: {artifact.status}</p>
          {artifact.issued_at && (
            <p>Issued: {new Date(artifact.issued_at).toLocaleString()}</p>
          )}
          <p>
            Current validity:{" "}
            {releaseStatus !== "RELEASED" || artifact.currently_valid === false
              ? "Not currently valid"
              : (artifact.effective_status ?? "Server validation required")}
          </p>
          {artifact.serial && <p>Serial: {artifact.serial}</p>}
          {artifact.checksum && (
            <p style={{ overflowWrap: "anywhere" }}>
              Checksum: {artifact.checksum}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
