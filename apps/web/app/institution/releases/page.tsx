"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../../lib/auth";
import {
  releaseApi,
  releaseStateMessage,
  type ResultRelease,
  type ReleaseQueueItem,
} from "./release-api";

export default function InstitutionReleasesPage() {
  const [releases, setReleases] = useState<ResultRelease[]>([]);
  const [queue, setQueue] = useState<ReleaseQueueItem[]>([]);
  const [message, setMessage] = useState("Loading release history…");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [predecessors, setPredecessors] = useState<Record<string, string>>({});
  const operations = useRef<Record<string, string>>({});
  const { organisationId, permissions } = useAuth();
  const canRead = permissions.includes("result.read");
  const canInspect = permissions.includes("exam_assignment.read");
  const canRelease = permissions.includes("result.release");

  useEffect(() => {
    let cancelled = false;
    setReleases([]);
    setQueue([]);
    setError(null);
    if (!organisationId || !canRead) {
      setMessage(
        "An active institution with result inspection permission is required.",
      );
      return;
    }
    setMessage("Loading release history…");
    Promise.all([
      releaseApi.list(organisationId),
      canInspect ? releaseApi.queue(organisationId) : Promise.resolve([]),
    ])
      .then(([items, results]) => {
        if (!cancelled) {
          setReleases(items);
          setQueue(results);
          setMessage("");
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setMessage("");
          setError(
            cause instanceof Error
              ? cause.message
              : "Release history could not be loaded.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [canInspect, canRead, organisationId, reload]);

  async function releaseResult(item: ReleaseQueueItem) {
    if (!organisationId || !canRelease) return;
    setBusy(item.version_id);
    setError(null);
    const predecessor = predecessors[item.version_id];
    const operation = (operations.current[
      `${item.version_id}:${predecessor ?? ""}`
    ] ??= crypto.randomUUID());
    try {
      await releaseApi.release(
        organisationId,
        item.version_id,
        operation,
        predecessor || undefined,
      );
      setMessage("Result release recorded.");
      setReload((value) => value + 1);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "The release was blocked.",
      );
    } finally {
      setBusy(null);
    }
  }

  const unreleased = queue.filter(
    (item) =>
      !releases.some(
        (release) => release.result_version_id === item.version_id,
      ),
  );

  return (
    <section>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution
      </p>
      <h1>Governed result releases</h1>
      <p>
        Release history is immutable. Corrected results appear as successor
        releases and never rewrite prior evidence.
      </p>
      {message && <p role="status">{message}</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Retry loading releases
          </button>
        </div>
      )}
      <h2>Result release queue</h2>
      {!canInspect && (
        <p>
          Your role can read release history. Assignment inspection is required
          to load the result queue.
        </p>
      )}
      {canInspect && !message && !unreleased.length && (
        <p>No inspected result is waiting for a release record.</p>
      )}
      <ul aria-label="Result release queue">
        {unreleased.map((item) => (
          <li key={item.version_id} style={{ marginBottom: "1.5rem" }}>
            <article>
              <h3>{item.exam_name ?? "Assessment result"}</h3>
              <p>Candidate: {item.candidate_name ?? "Candidate"}</p>
              <p>Result status: {item.status}</p>
              <p>
                Version: <code>{item.version_id}</code>
              </p>
              {canRelease &&
                releases.some(
                  (release) =>
                    release.attempt_id === item.attempt_id &&
                    release.status === "RELEASED",
                ) && (
                  <label>
                    Predecessor release for a governed correction{" "}
                    <select
                      value={predecessors[item.version_id] ?? ""}
                      onChange={(event) =>
                        setPredecessors((current) => ({
                          ...current,
                          [item.version_id]: event.target.value,
                        }))
                      }
                    >
                      <option value="">
                        Select the authoritative predecessor
                      </option>
                      {releases
                        .filter(
                          (release) =>
                            release.attempt_id === item.attempt_id &&
                            release.status === "RELEASED",
                        )
                        .map((release) => (
                          <option key={release.id} value={release.id}>
                            {release.id}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
              {["APPROVED", "RELEASED"].includes(item.status) ? (
                canRelease ? (
                  <button
                    disabled={busy !== null}
                    onClick={() => void releaseResult(item)}
                  >
                    {busy === item.version_id
                      ? "Recording release…"
                      : item.status === "RELEASED"
                        ? "Record governed release"
                        : "Release approved result"}
                  </button>
                ) : (
                  <p>Your role cannot release results.</p>
                )
              ) : (
                <p>
                  Blocked: this result must complete human review and approval
                  before release.
                </p>
              )}
            </article>
          </li>
        ))}
      </ul>
      <h2>Release history</h2>
      {!message && !releases.length && <p>No release records are available.</p>}
      <ul aria-label="Result release history">
        {releases.map((release) => (
          <li key={release.id} style={{ marginTop: "1rem" }}>
            <article>
              <h2>{release.classification} result</h2>
              <p>Status: {release.status}</p>
              <p>Candidate: {release.candidate_user_id}</p>
              <p>Released: {new Date(release.released_at).toLocaleString()}</p>
              <p>
                Effective status: {release.effective_status ?? release.status}
              </p>
              <p>
                {releaseStateMessage(
                  release.effective_status ?? release.status,
                )}
              </p>
              <Link href={`/institution/releases/${release.id}`}>
                Inspect release detail and artifacts
              </Link>
            </article>
          </li>
        ))}
      </ul>
    </section>
  );
}
