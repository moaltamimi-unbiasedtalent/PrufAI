"use client";

import Link from "next/link";
import { pilotActive } from "../../../lib/access";
import { useAuth } from "../../../lib/auth";
import { useEffect, useState } from "react";
import {
  releaseApi,
  releaseStateMessage,
  type ResultRelease,
} from "../../institution/releases/release-api";

export default function CandidateResultsPage() {
  const restricted = pilotActive(useAuth().user);
  const [results, setResults] = useState<ResultRelease[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResults([]);
    releaseApi
      .candidateList()
      .then((items) => {
        if (!cancelled) setResults(items);
      })
      .catch(() => {
        if (!cancelled) setError("Released results are unavailable right now.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate
      </p>
      <h1>Released results</h1>
      {!restricted && (
        <p>
          <Link href="/candidate/results/appeals">My appeal history →</Link>
        </p>
      )}
      {loading && <p role="status">Loading released results…</p>}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Retry released results
          </button>
        </div>
      )}
      {!loading && !error && results.length === 0 && (
        <p>No released result is available.</p>
      )}
      <ul aria-label="Released assessment results">
        {results.map((result) => (
          <li key={result.id}>
            <article>
              <h2>Assessment result</h2>
              <p>Status: {result.status}</p>
              <p>
                Effective status: {result.effective_status ?? result.status}
              </p>
              <p>
                {releaseStateMessage(result.effective_status ?? result.status)}
              </p>
              <p>Classification: {result.classification}</p>
              <p>Released: {new Date(result.released_at).toLocaleString()}</p>
              <p>
                <Link href={`/candidate/results/releases/${result.id}`}>
                  View result and Statement of Results
                </Link>
              </p>
              {!restricted &&
                (result.effective_status ?? result.status) === "RELEASED" && (
                  <Link
                    href={`/candidate/results/appeal?resultVersionId=${encodeURIComponent(result.result_version_id)}`}
                  >
                    Appeal this released result
                  </Link>
                )}
            </article>
          </li>
        ))}
      </ul>
    </main>
  );
}
