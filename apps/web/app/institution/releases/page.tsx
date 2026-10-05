"use client";

import { useEffect, useState } from "react";
import { releaseApi, type ResultRelease } from "./release-api";

export default function InstitutionReleasesPage() {
  const [releases, setReleases] = useState<ResultRelease[]>([]);
  const [message, setMessage] = useState("Loading release history…");
  const organisationId = process.env.NEXT_PUBLIC_ORGANISATION_ID ?? "";

  useEffect(() => {
    if (!organisationId) {
      setMessage("Organisation context is required.");
      return;
    }
    releaseApi
      .list(organisationId)
      .then((items) => {
        setReleases(items);
        setMessage("");
      })
      .catch(() => setMessage("Release history could not be loaded."));
  }, [organisationId]);

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution
      </p>
      <h1>Governed result releases</h1>
      <p>
        Release history is immutable. Corrected results appear as successor
        releases and never rewrite prior evidence.
      </p>
      {message && <p role="status">{message}</p>}
      <ul aria-label="Result release history">
        {releases.map((release) => (
          <li key={release.id} style={{ marginTop: "1rem" }}>
            <article>
              <h2>{release.classification} result</h2>
              <p>Status: {release.status}</p>
              <p>Candidate: {release.candidate_user_id}</p>
              <p>Released: {new Date(release.released_at).toLocaleString()}</p>
            </article>
          </li>
        ))}
      </ul>
    </main>
  );
}
