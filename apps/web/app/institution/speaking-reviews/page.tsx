"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { speakingReviewApi, type SpeakingReviewSummary } from "./review-api";

export default function SpeakingReviewQueuePage() {
  const [reviews, setReviews] = useState<SpeakingReviewSummary[]>([]);
  const [message, setMessage] = useState("Loading review queue…");
  const organisationId = process.env.NEXT_PUBLIC_ORGANISATION_ID ?? "";
  useEffect(() => {
    if (!organisationId) {
      setMessage("Organisation context is required.");
      return;
    }
    speakingReviewApi
      .list(organisationId)
      .then((items) => {
        setReviews(items);
        setMessage("");
      })
      .catch(() => setMessage("Speaking review queue could not be loaded."));
  }, [organisationId]);
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>Institution</p>
      <h1>Speaking review queue</h1>
      <p>Original audio is primary evidence. Transcriptions are derived evidence.</p>
      {message && <p role="status">{message}</p>}
      <table>
        <caption className="sr-only">Pending speaking assessments</caption>
        <thead><tr><th scope="col">Assessment</th><th scope="col">Attempt</th><th scope="col">Status</th></tr></thead>
        <tbody>{reviews.map((review) => <tr key={review.assessment_id}>
          <td><Link href={`/institution/speaking-reviews/${review.assessment_id}`}>{review.assessment_id}</Link></td>
          <td>{review.attempt_id}</td><td>{review.status}</td>
        </tr>)}</tbody>
      </table>
    </main>
  );
}
