"use client";

import { useEffect, useState } from "react";
import {
  lidOutcomeLabel,
  lidPurposeLabel,
  type LidPracticeResult,
} from "../../../../lib/lid-practice";
import { CandidateApiError, candidateApi } from "../../api";

export function LidPracticeResultPanel({ attemptId }: { attemptId: string }) {
  const [result, setResult] = useState<LidPracticeResult | null>(null);
  const [state, setState] = useState<"loading" | "pending" | "error">(
    "loading",
  );
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let current = true;
    setState("loading");
    setResult(null);
    void candidateApi
      .lidPracticeResult(attemptId)
      .then((value) => {
        if (current) setResult(value);
      })
      .catch((cause: unknown) => {
        if (!current) return;
        setState(
          cause instanceof CandidateApiError &&
            cause.code === "LID_PRACTICE_RESULT_PENDING"
            ? "pending"
            : "error",
        );
      });
    return () => {
      current = false;
    };
  }, [attemptId, reload]);

  return (
    <section aria-labelledby="lid-practice-result">
      <h2 id="lid-practice-result">LiD practice result</h2>
      <p>NOT OFFICIAL · NOT CERTIFIED</p>
      {!result && state === "loading" && (
        <p role="status">Loading the practice result…</p>
      )}
      {!result && state !== "loading" && (
        <>
          <p role={state === "error" ? "alert" : "status"}>
            {state === "pending"
              ? "Your submission is saved. The practice result will appear after your institution reviews and releases it."
              : "The practice result could not be loaded."}
          </p>
          <button type="button" onClick={() => setReload((value) => value + 1)}>
            Refresh practice result
          </button>
        </>
      )}
      {result && (
        <>
          <p>
            {result.raw_correct} of {result.total_items} answers correct
          </p>
          {result.answered_count < result.total_items && (
            <p>
              {result.answered_count} questions answered. The numerical previews
              require a complete response set.
            </p>
          )}
          <p>Administrative recognition: {result.administrative_recognition}</p>
          <ul aria-label="Distinct numerical previews">
            {result.contexts.map((context) => (
              <li key={context.purpose}>
                <h3>{lidPurposeLabel[context.purpose]}</h3>
                <p>
                  {context.threshold}/{result.total_items} ·{" "}
                  {lidOutcomeLabel[context.numerical_outcome]}
                </p>
                <p>
                  Rule version: {context.rule_version_label ?? "Unavailable"}
                </p>
                <p>
                  Legal effective date:{" "}
                  {context.legal_effective_date ??
                    context.legal_effective_from ??
                    "Unavailable"}
                </p>
                {context.citations.length > 0 && (
                  <ul aria-label="Exact legal source versions">
                    {context.citations.map((citation) => (
                      <li
                        key={`${citation.source_version_id}:${citation.locator}`}
                      >
                        {citation.locator} ·{" "}
                        <code>{citation.source_version_id}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          <details>
            <summary>Scoring evidence</summary>
            <dl>
              <dt>Scoring run</dt>
              <dd>{result.scoring_run_id}</dd>
              <dt>Result version</dt>
              <dd>{result.result_version_id}</dd>
            </dl>
          </details>
        </>
      )}
    </section>
  );
}
