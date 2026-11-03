"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  releaseApi,
  releaseStateMessage,
  type ResultRelease,
} from "../../institution/releases/release-api";

export default function CandidateResultsPage() {
  const [results, setResults] = useState<ResultRelease[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
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
  }, []);

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate
      </p>
      <h1>Released results</h1>
      {loading && <p role="status">Loading released results…</p>}
      {error && <p role="alert">{error}</p>}
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
              {(result.effective_status ?? result.status) === "RELEASED" && (
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
