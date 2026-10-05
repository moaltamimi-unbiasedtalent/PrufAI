"use client";

import { FormEvent, useState } from "react";

export default function CandidateAppealPage() {
  const [message, setMessage] = useState("");
  const [resultVersionId, setResultVersionId] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const response = await fetch(
      `/api/v1/candidate/results/${resultVersionId}/appeals`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: form.get("category"),
          grounds: form.get("grounds"),
          operation_id: crypto.randomUUID(),
        }),
      },
    );
    setMessage(
      response.ok
        ? "Appeal submitted. Its status is now visible in your appeal history."
        : "This result is not eligible for appeal.",
    );
  }
  return (
    <main style={{ maxWidth: 720, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate
      </p>
      <h1>Appeal a released result</h1>
      <p>
        Appeals are linked to the exact released result version. Submitted text
        cannot be edited destructively.
      </p>
      <form onSubmit={(event) => void submit(event)}>
        <label htmlFor="result-version">Result version ID</label>
        <input
          id="result-version"
          value={resultVersionId}
          onChange={(event) => setResultVersionId(event.target.value)}
          required
        />
        <label htmlFor="category">Category</label>
        <input id="category" name="category" maxLength={80} required />
        <label htmlFor="grounds">Grounds</label>
        <textarea
          id="grounds"
          name="grounds"
          maxLength={10000}
          required
          rows={8}
        />
        <button type="submit">Submit appeal</button>
      </form>
      {message && <p role="status">{message}</p>}
    </main>
  );
}
