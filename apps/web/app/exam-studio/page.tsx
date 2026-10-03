"use client";

import { useState } from "react";

const stages = ["Draft", "Blueprint", "Assign items", "Validate", "Review", "Publish"];

export default function ExamStudioPage() {
  const [selectedStage, setSelectedStage] = useState("Draft");

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>Administration</p>
      <h1>Exam Studio</h1>
      <p>
        Assemble an immutable assessment from one exact Standard Pack version and eligible
        Question Bank item versions. Server validation remains authoritative.
      </p>
      <section aria-labelledby="workflow-heading" style={{ marginTop: "2rem" }}>
        <h2 id="workflow-heading">Governed workflow</h2>
        <nav aria-label="Exam Studio workflow">
          <ol style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", padding: 0 }}>
            {stages.map((stage) => (
              <li key={stage} style={{ listStyle: "none" }}>
                <button type="button" aria-pressed={selectedStage === stage} onClick={() => setSelectedStage(stage)}>
                  {stage}
                </button>
              </li>
            ))}
          </ol>
        </nav>
      </section>
      <section aria-labelledby="selection-heading" style={{ marginTop: "2rem" }}>
        <h2 id="selection-heading">{selectedStage}</h2>
        <p>
          Select an exact ExamVersion and StandardPackVersion. Candidate-safe preview is a
          separate server response and never relies on hiding answer keys in the browser.
        </p>
        <button type="button" disabled>
          Connect to governed Exam API
        </button>
      </section>
    </main>
  );
}
