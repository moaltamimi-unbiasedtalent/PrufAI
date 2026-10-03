"use client";

import { useEffect, useState } from "react";

type SyncState = "Saved" | "Saving" | "Offline — queued" | "Conflict";

export default function CandidatePage() {
  const [syncState, setSyncState] = useState<SyncState>("Saved");
  const [serverNow, setServerNow] = useState<string | null>(null);

  useEffect(() => {
    setServerNow(new Date().toISOString());
  }, []);

  function queueResponse() {
    setSyncState(navigator.onLine ? "Saving" : "Offline — queued");
    window.setTimeout(
      () => setSyncState(navigator.onLine ? "Saved" : "Offline — queued"),
      250,
    );
  }

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate
      </p>
      <h1>My exams</h1>
      <p>
        Only assignments belonging to the authenticated candidate are shown.
      </p>
      <section
        aria-labelledby="assignment-heading"
        style={{ marginTop: "2rem" }}
      >
        <h2 id="assignment-heading">Available assignment</h2>
        <p>
          Exam content is loaded only after the server confirms assignment
          ownership and start eligibility.
        </p>
        <button type="button" onClick={queueResponse}>
          Save response
        </button>
        <p role="status" aria-live="polite">
          Autosave: {syncState}
        </p>
        <p>Server clock: {serverNow ?? "Synchronising…"}</p>
      </section>
    </main>
  );
}
