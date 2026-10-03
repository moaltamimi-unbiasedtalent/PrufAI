"use client";

import { use, useEffect, useState } from "react";
import { speakingReviewApi, type SpeakingReview } from "../review-api";

export default function SpeakingReviewWorkspace({ params }: { params: Promise<{ assessmentId: string }> }) {
  const { assessmentId } = use(params);
  const [review, setReview] = useState<SpeakingReview | null>(null);
  const [message, setMessage] = useState("Loading assessment…");
  const organisationId = process.env.NEXT_PUBLIC_ORGANISATION_ID ?? "";
  useEffect(() => {
    if (!organisationId) { setMessage("Organisation context is required."); return; }
    speakingReviewApi.detail(organisationId, assessmentId)
      .then((value) => { setReview(value); setMessage(""); })
      .catch(() => setMessage("Speaking assessment could not be loaded."));
  }, [organisationId, assessmentId]);
  return <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
    <h1>Speaking assessment review</h1>
    {message && <p role="status">{message}</p>}
    {review && <>
      <p><strong>Original audio — PRIMARY EVIDENCE</strong></p>
      <p>Derived transcription: {review.derived_transcription ? "available" : "not available"}</p>
      <p>AI proposal remains distinct from human-approved values.</p>
      <h2>Criteria</h2>
      <ul>{review.criteria.map((criterion) => <li key={String(criterion.criterion_code)}>
        {String(criterion.criterion_code)}: AI {String(criterion.proposed_points)}
      </li>)}</ul>
    </>}
  </main>;
}
