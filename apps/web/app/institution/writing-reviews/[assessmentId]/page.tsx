"use client";

import { useAuth } from "../../../../lib/auth";

import { useEffect, useState } from "react";
import { writingReviewApi, type WritingReview } from "../review-api";

type Props = { params: Promise<{ assessmentId: string }> };

export default function WritingReviewWorkspacePage({ params }: Props) {
  const [assessmentId, setAssessmentId] = useState("");
  const [review, setReview] = useState<WritingReview | null>(null);
  const [message, setMessage] = useState("");
  const organisationId = useAuth().organisationId ?? "";

  useEffect(() => {
    void params.then(({ assessmentId: id }) => {
      setAssessmentId(id);
      if (organisationId) {
        writingReviewApi
          .detail(organisationId, id)
          .then(setReview)
          .catch(() => setMessage("Review could not be loaded."));
      }
    });
  }, [organisationId, params]);

  async function decide(
    decision: "APPROVE" | "REJECTED" | "CHANGES_REQUESTED",
  ) {
    if (!review || !organisationId) return;
    try {
      if (decision === "APPROVE")
        await writingReviewApi.approve(organisationId, review.version_id);
      else
        await writingReviewApi.decide(
          organisationId,
          review.version_id,
          decision,
        );
      setMessage("Review state saved. Reload to confirm the current checksum.");
    } catch (error) {
      setMessage(
        error instanceof Error && error.message.includes("409")
          ? "Review changed; reload required."
          : "Review action failed.",
      );
    }
  }

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution review
      </p>
      <h1>Writing assessment {assessmentId}</h1>
      {message && <p role="alert">{message}</p>}
      {!review ? (
        <p role="status">
          {organisationId
            ? "Loading review…"
            : "Organisation context is required."}
        </p>
      ) : (
        <>
          <section aria-labelledby="writing-heading">
            <h2 id="writing-heading">Candidate writing</h2>
            <p>{review.candidate_text}</p>
          </section>
          <section aria-labelledby="proposal-heading">
            <h2 id="proposal-heading">AI PROPOSAL</h2>
            {review.criteria.map((criterion) => (
              <p key={String(criterion.criterion_code)}>
                {String(criterion.criterion_code)}:{" "}
                {String(criterion.proposed_level)} /{" "}
                {String(criterion.proposed_points)}
              </p>
            ))}
          </section>
          <section aria-labelledby="human-heading">
            <h2 id="human-heading">HUMAN APPROVED VALUE</h2>
            {review.criteria.map((criterion) => (
              <p key={`human-${String(criterion.criterion_code)}`}>
                {String(criterion.criterion_code)}:{" "}
                {String(criterion.approved_level ?? "Not approved")} /{" "}
                {String(criterion.approved_points ?? "—")}
              </p>
            ))}
          </section>
          <fieldset>
            <legend>Review action</legend>
            <button type="button" onClick={() => void decide("APPROVE")}>
              Approve
            </button>
            <button
              type="button"
              onClick={() => void decide("CHANGES_REQUESTED")}
            >
              Request changes
            </button>
            <button type="button" onClick={() => void decide("REJECTED")}>
              Reject
            </button>
          </fieldset>
        </>
      )}
    </main>
  );
}
