"use client";

import { useEffect, useState } from "react";
import { appealApi, type AppealDetail } from "../appeal-api";

type Props = { params: Promise<{ appealId: string }> };

export default function AppealWorkspacePage({ params }: Props) {
  const [appeal, setAppeal] = useState<AppealDetail | null>(null);
  const [message, setMessage] = useState("Loading appeal…");
  const organisationId = process.env.NEXT_PUBLIC_ORGANISATION_ID ?? "";
  useEffect(() => {
    void params.then(({ appealId }) => {
      if (!organisationId) {
        setMessage("Organisation context is required.");
        return;
      }
      appealApi
        .detail(organisationId, appealId)
        .then((value) => {
          setAppeal(value);
          setMessage("");
        })
        .catch(() => setMessage("Appeal could not be loaded."));
    });
  }, [organisationId, params]);
  async function startReview() {
    if (!appeal || !organisationId) return;
    try {
      await appealApi.review(organisationId, appeal.id);
      setMessage("Review started. Reload to confirm the current version.");
    } catch {
      setMessage("The appeal changed or is not available for review.");
    }
  }
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution review
      </p>
      <h1>Appeal workspace</h1>
      {message && <p role="status">{message}</p>}
      {appeal && (
        <>
          <section aria-labelledby="identity">
            <h2 id="identity">Pinned result evidence</h2>
            <dl>
              <dt>Appeal</dt>
              <dd>{appeal.id}</dd>
              <dt>Status</dt>
              <dd>{appeal.status}</dd>
              <dt>Result version</dt>
              <dd>{appeal.challenged_result_version_id}</dd>
              <dt>Result checksum</dt>
              <dd>{appeal.challenged_result_checksum}</dd>
              <dt>Evidence checksum</dt>
              <dd>{appeal.evidence_bundle_checksum ?? "Not available"}</dd>
            </dl>
          </section>
          <section aria-labelledby="grounds">
            <h2 id="grounds">Candidate grounds</h2>
            <p>{appeal.grounds}</p>
          </section>
          <button type="button" onClick={() => void startReview()}>
            Start review
          </button>
        </>
      )}
    </main>
  );
}
