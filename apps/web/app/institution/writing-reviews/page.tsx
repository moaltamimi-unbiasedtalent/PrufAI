"use client";

import { useAuth } from "../../../lib/auth";

import Link from "next/link";
import { useEffect, useState } from "react";
import { writingReviewApi, type WritingReviewSummary } from "./review-api";

export default function WritingReviewQueuePage() {
  const [reviews, setReviews] = useState<WritingReviewSummary[]>([]);
  const [message, setMessage] = useState("Loading review queue…");
  const { organisationId: activeOrganisationId, demoMode } = useAuth();
  const organisationId = activeOrganisationId ?? "";
  useEffect(() => {
    if (!organisationId) {
      setMessage("Organisation context is required.");
      return;
    }
    writingReviewApi
      .list(organisationId)
      .then((items) => {
        setReviews(items);
        setMessage("");
      })
      .catch(() => setMessage("Review queue could not be loaded."));
  }, [organisationId]);
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution
      </p>
      <h1>Writing review queue</h1>
      <p>
        Authorised reviewers see safe assessment summaries. Candidate writing is
        not returned in this list.
      </p>
      {message && <p role="status">{message}</p>}
      <table>
        <caption className="sr-only">Pending writing assessments</caption>
        <thead>
          <tr>
            <th scope="col">Assessment</th>
            <th scope="col">Attempt</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {!message && reviews.length === 0 && (
            <tr>
              <td colSpan={3}>
                No writing assessments are available for this organisation.
                {demoMode &&
                  " This demo includes objective practice evidence only; no submitted writing has been seeded."}
              </td>
            </tr>
          )}
          {reviews.map((review) => (
            <tr key={review.id}>
              <td>
                <Link href={`/institution/writing-reviews/${review.id}`}>
                  {review.id}
                </Link>
              </td>
              <td>{review.attempt_id}</td>
              <td>{review.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
