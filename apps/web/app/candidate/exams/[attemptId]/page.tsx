"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

export default function CandidateExamRuntimePage() {
  const params = useParams<{ attemptId: string }>();
  const [response, setResponse] = useState("");
  const [status, setStatus] = useState("Saved");

  function saveResponse() {
    setStatus(navigator.onLine ? "Saving" : "Offline — queued");
    window.setTimeout(
      () => setStatus(navigator.onLine ? "Saved" : "Offline — queued"),
      250,
    );
  }

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate exam
      </p>
      <h1>Runtime</h1>
      <p>Attempt {params.attemptId} · Server time controls the deadline.</p>
      <p role="status" aria-live="polite">
        Autosave: {status}
      </p>
      <label htmlFor="candidate-response">Response</label>
      <textarea
        id="candidate-response"
        value={response}
        onChange={(event) => setResponse(event.target.value)}
        rows={8}
        maxLength={20000}
        style={{ display: "block", width: "100%", marginTop: "0.5rem" }}
      />
      <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
        <button type="button" onClick={saveResponse}>
          Save response
        </button>
        <button type="button">Submit attempt</button>
      </div>
    </main>
  );
}
