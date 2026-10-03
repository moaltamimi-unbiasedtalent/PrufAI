"use client";

import { useParams } from "next/navigation";
import React, { useEffect, useId, useMemo, useState } from "react";
import {
  RuntimeTimer,
  runtimeStateMessage,
  type ResponseMode,
  type RuntimeState,
} from "./runtime-ux";

const options = [
  { id: "a", label: "Option A" },
  { id: "b", label: "Option B" },
  { id: "c", label: "Option C" },
];

export default function CandidateExamRuntimePage() {
  const params = useParams<{ attemptId: string }>();
  const promptId = useId();
  const responseHelpId = `${promptId}-help`;
  const errorId = `${promptId}-error`;
  const [mode, setMode] = useState<ResponseMode>("LONG_TEXT");
  const [response, setResponse] = useState<string | string[]>("");
  const [status, setStatus] = useState<RuntimeState>("Saved");
  const [online, setOnline] = useState(true);
  const [serverDeadlineAt] = useState<number | null>(null);

  useEffect(() => {
    const updateConnection = () => setOnline(navigator.onLine);
    updateConnection();
    window.addEventListener("online", updateConnection);
    window.addEventListener("offline", updateConnection);
    return () => {
      window.removeEventListener("online", updateConnection);
      window.removeEventListener("offline", updateConnection);
    };
  }, []);

  const editable = status !== "Expired" && status !== "Submitted";
  const importantStatus = [
    "Offline — queued",
    "Conflict",
    "Expired",
    "Submitted",
  ].includes(status);
  const errorMessage = runtimeStateMessage(status);

  const selectedOptions = useMemo(
    () => (Array.isArray(response) ? response : response ? [response] : []),
    [response],
  );

  function saveResponse() {
    if (!editable) return;
    setStatus(online ? "Saving" : "Offline — queued");
    if (online) {
      window.setTimeout(() => setStatus("Saved"), 250);
    }
  }

  function submitAttempt() {
    if (editable) setStatus("Submitted");
  }

  function updateChoice(choice: string, multiple: boolean) {
    if (!editable) return;
    if (!multiple) {
      setResponse(choice);
      return;
    }
    setResponse((current) => {
      const values = Array.isArray(current) ? current : [];
      return values.includes(choice)
        ? values.filter((value) => value !== choice)
        : [...values, choice];
    });
  }

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate exam
      </p>
      <h1>Runtime</h1>
      <p>Attempt {params.attemptId} · Server time controls the deadline.</p>

      <RuntimeTimer deadlineAt={serverDeadlineAt} />

      <section aria-labelledby="connection-heading">
        <h2 id="connection-heading">Connection</h2>
        <p>
          {online
            ? "Online — responses can sync with the server."
            : "Offline — responses are queued locally until reconnection."}
        </p>
      </section>

      <section aria-labelledby="sync-heading">
        <h2 id="sync-heading">Response sync</h2>
        <p
          role={importantStatus ? "alert" : "status"}
          aria-live={importantStatus ? "polite" : "off"}
        >
          Autosave: {status}
        </p>
        {errorMessage && (
          <p id={errorId} role="alert">
            {errorMessage}
          </p>
        )}
      </section>

      <section aria-labelledby="question-heading">
        <h2 id="question-heading">Question</h2>
        <p id={promptId}>Which response best answers this question?</p>
        <p id={responseHelpId}>
          Choose one response mode and provide your answer.
        </p>
        <label htmlFor="response-mode">Response mode</label>
        <select
          id="response-mode"
          value={mode}
          onChange={(event) => {
            setMode(event.target.value as ResponseMode);
            setResponse("");
          }}
          disabled={!editable}
        >
          <option value="SELECT_ONE">Select one</option>
          <option value="SELECT_MULTIPLE">Select multiple</option>
          <option value="TEXT">Short text</option>
          <option value="LONG_TEXT">Long text</option>
        </select>

        {(mode === "SELECT_ONE" || mode === "SELECT_MULTIPLE") && (
          <fieldset
            aria-describedby={`${promptId} ${responseHelpId}${errorMessage ? ` ${errorId}` : ""}`}
          >
            <legend>
              {mode === "SELECT_ONE"
                ? "Select one answer"
                : "Select all answers that apply"}
            </legend>
            {options.map((option) => (
              <label
                key={option.id}
                style={{ display: "block", marginTop: "0.5rem" }}
              >
                <input
                  type={mode === "SELECT_ONE" ? "radio" : "checkbox"}
                  name="candidate-response"
                  value={option.id}
                  checked={selectedOptions.includes(option.id)}
                  onChange={() =>
                    updateChoice(option.id, mode === "SELECT_MULTIPLE")
                  }
                  disabled={!editable}
                />{" "}
                {option.label}
              </label>
            ))}
          </fieldset>
        )}

        {mode === "TEXT" && (
          <label htmlFor="candidate-short-response">
            Short response
            <input
              id="candidate-short-response"
              value={
                typeof response === "string" ? response : response.join(", ")
              }
              onChange={(event) => setResponse(event.target.value)}
              aria-describedby={`${promptId} ${responseHelpId}${errorMessage ? ` ${errorId}` : ""}`}
              aria-invalid={status === "Conflict"}
              disabled={!editable}
              maxLength={4000}
              style={{ display: "block", width: "100%", marginTop: "0.5rem" }}
            />
          </label>
        )}

        {mode === "LONG_TEXT" && (
          <label htmlFor="candidate-long-response">
            Long response
            <textarea
              id="candidate-long-response"
              value={
                typeof response === "string" ? response : response.join(", ")
              }
              onChange={(event) => setResponse(event.target.value)}
              aria-describedby={`${promptId} ${responseHelpId}${errorMessage ? ` ${errorId}` : ""}`}
              aria-invalid={status === "Conflict"}
              disabled={!editable}
              rows={8}
              maxLength={20000}
              style={{ display: "block", width: "100%", marginTop: "0.5rem" }}
            />
          </label>
        )}
      </section>

      <div style={{ display: "flex", gap: "0.75rem", marginTop: "1rem" }}>
        <button type="button" onClick={saveResponse} disabled={!editable}>
          Save response
        </button>
        <button type="button" onClick={submitAttempt} disabled={!editable}>
          Submit attempt
        </button>
        {status === "Conflict" && (
          <button type="button" onClick={() => setStatus("Saved")}>
            Reload latest response
          </button>
        )}
      </div>
    </main>
  );
}
