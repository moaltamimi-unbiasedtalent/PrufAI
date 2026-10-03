"use client";

import React, { useEffect, useState } from "react";
import { candidateApi } from "../../candidate/api";
import Link from "next/link";

export default function CandidateResultsPage() {
  const [results, setResults] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    candidateApi
      .results()
      .then(setResults)
      .catch(() => setError("Released results are unavailable right now."))
      .finally(() => setLoading(false));
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
          <li key={String(result.result_version_id)}>
            <article>
              <h2>Assessment result</h2>
              <p>Status: Released</p>
              <p>Outcome: {String(result.outcome ?? "Undetermined")}</p>
              <p>
                Score: {String(result.raw_awarded_points ?? "—")} /{" "}
                {String(result.raw_maximum_points ?? "—")}
              </p>
              <Link href="/candidate/results/appeal">
                Appeal a released result
              </Link>
            </article>
          </li>
        ))}
      </ul>
    </main>
  );
}
