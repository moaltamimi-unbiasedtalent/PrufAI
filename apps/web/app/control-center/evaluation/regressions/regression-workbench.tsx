"use client";

import Link from "next/link";
import React, { useRef, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import formCss from "../../../institution/assessment.module.css";
import css from "../../control-center.module.css";
import { MetadataTable } from "../../metadata";
import { evaluationApi, type EvaluationRunDetail } from "../evaluation-api";
import {
  exactPin,
  regressionApi,
  type ComparisonDimension,
  type EvidenceDecision,
  type RegressionComparison,
  type RegressionVersion,
  type VersionAction,
  type VersionKind,
} from "./regression-api";
import {
  RegressionComparisonDetails,
  RegressionVersionDetails,
} from "./regression-details";

function field(data: FormData, name: string) {
  return String(data.get(name) ?? "").trim();
}

function PinFields({
  name,
  label,
  required = true,
}: {
  name: string;
  label: string;
  required?: boolean;
}) {
  return (
    <>
      <label>
        {label} ID <input name={`${name}_id`} required={required} />
      </label>
      <label>
        {label} checksum{" "}
        <input
          name={`${name}_checksum`}
          required={required}
          pattern={required ? "[0-9a-f]{64}" : undefined}
        />
      </label>
    </>
  );
}

type ComparisonSide = "baseline" | "candidate";

function AdditionalRunFields({
  side,
  busy,
}: {
  side: ComparisonSide;
  busy: boolean;
}) {
  const [count, setCount] = useState(0);
  return (
    <fieldset>
      <legend>Additional {side} runs</legend>
      {Array.from({ length: count }, (_, index) => (
        <PinFields
          key={index}
          name={`${side}_additional`}
          label={`Additional ${side} run ${index + 1}`}
          required={false}
        />
      ))}
      <button
        type="button"
        disabled={busy || count >= 99}
        onClick={() => setCount((value) => Math.min(99, value + 1))}
      >
        Add {side} run
      </button>
      {count > 0 && (
        <button
          type="button"
          disabled={busy}
          onClick={() => setCount((value) => value - 1)}
        >
          Remove last {side} run
        </button>
      )}
    </fieldset>
  );
}

export function additionalRuns(data: FormData, side: ComparisonSide) {
  const ids = data.getAll(`${side}_additional_id`);
  const checksums = data.getAll(`${side}_additional_checksum`);
  if (ids.length !== checksums.length) {
    throw new Error(
      `Each additional ${side} run requires an exact ID and checksum.`,
    );
  }
  return ids.map((id, index) =>
    exactPin(String(id).trim(), String(checksums[index]).trim()),
  );
}

export function EvidenceRecommendationSelect({
  hardGate,
}: {
  hardGate: RegressionComparison["run"]["hard_gate"];
}) {
  const canAccept = hardGate === "PASS";
  return (
    <label>
      Evidence recommendation{" "}
      <select
        name="decision"
        defaultValue={canAccept ? "ACCEPTED" : "CHANGES_REQUIRED"}
      >
        {canAccept && <option value="ACCEPTED">ACCEPTED</option>}
        <option value="REJECTED">REJECTED</option>
        <option value="CHANGES_REQUIRED">CHANGES_REQUIRED</option>
      </select>
    </label>
  );
}

function VersionActions({
  version,
  busy,
  apply,
}: {
  version: RegressionVersion;
  busy: boolean;
  apply: (action: VersionAction, reviewReference?: string) => void;
}) {
  const [selected, setSelected] = useState<VersionAction | null>(null);
  const action =
    selected && version.allowed_actions.includes(selected)
      ? selected
      : version.allowed_actions[0];
  if (!action)
    return (
      <p>
        No lifecycle action is available to this identity for the selected
        version.
      </p>
    );
  const reviewing = action === "approve" || action === "reject";
  return (
    <form
      className={formCss.form}
      aria-label="Review version lifecycle"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        apply(action, reviewing ? field(data, "review_reference") : undefined);
      }}
    >
      <fieldset disabled={busy}>
        <legend>Version lifecycle</legend>
        <label>
          Version action
          <select
            value={action}
            onChange={(event) =>
              setSelected(event.target.value as VersionAction)
            }
          >
            {version.allowed_actions.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        {reviewing && (
          <label>
            Version review reference{" "}
            <input name="review_reference" required maxLength={200} />
          </label>
        )}
        <button type="submit">Apply version action</button>
      </fieldset>
    </form>
  );
}

function Workbench({ canManage }: { canManage: boolean }) {
  const [source, setSource] = useState<EvaluationRunDetail | null>(null);
  const [version, setVersion] = useState<RegressionVersion | null>(null);
  const [comparison, setComparison] = useState<RegressionComparison | null>(
    null,
  );
  const [caseCount, setCaseCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const pending = useRef(false);
  const operations = useRef(new Map<string, string>());

  function operation(action: string, body: object) {
    const key = `${action}:${JSON.stringify(body)}`;
    const known = operations.current.get(key);
    if (known) return known;
    const id = crypto.randomUUID();
    operations.current.set(key, id);
    return id;
  }

  async function perform(task: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await task();
    } catch (cause: unknown) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The operation could not be completed.",
      );
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  function submit(
    task: (data: FormData) => Promise<void>,
  ): React.FormEventHandler<HTMLFormElement> {
    return (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      void perform(() => task(data));
    };
  }

  const findings =
    source?.failures.flatMap((failure) =>
      (failure.failed_checks ?? []).flatMap((check) =>
        failure.result_id && failure.result_checksum
          ? [
              {
                caseId: failure.case_id,
                result: {
                  id: failure.result_id,
                  checksum: failure.result_checksum,
                },
                check,
              },
            ]
          : [],
      ),
    ) ?? [];

  return (
    <>
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      {status && (
        <p role="status" className={css.notice}>
          {status}
        </p>
      )}
      {busy && (
        <p role="status">
          Loading exact receipts or recording the requested action…
        </p>
      )}

      {canManage && (
        <section className={css.card} aria-label="Promote a measured failure">
          <h2>Promote a measured failure</h2>
          <form
            className={formCss.form}
            aria-label="Load source run"
            onSubmit={submit(async (data) => {
              setSource(null);
              const result = await evaluationApi.run(field(data, "run_id"));
              setSource(result);
            })}
          >
            <label>
              Source run ID <input name="run_id" required />
            </label>
            <button type="submit" disabled={busy}>
              Load source run
            </button>
          </form>
          {source && (
            <p>
              Source run {source.run.id} · technical gate {source.run.hard_gate}
            </p>
          )}
          {source && findings.length === 0 && (
            <p>
              This run has no measured failed check receipt available for
              promotion.
            </p>
          )}
          {source?.run.checksum && findings.length > 0 && (
            <form
              className={formCss.form}
              aria-label="Propose regression case"
              onSubmit={submit(async (data) => {
                const finding = findings.find(
                  (item) =>
                    `${item.result.id}:${item.check}` ===
                    field(data, "failure"),
                );
                if (!finding || !source.run.checksum)
                  throw new Error("Select an exact measured failed check.");
                const body = {
                  source_run: exactPin(source.run.id, source.run.checksum),
                  source_result: finding.result,
                  failed_check: finding.check,
                };
                const receipt = await regressionApi.promote({
                  ...body,
                  operation_id: operation("promote", body),
                });
                setVersion(receipt);
                setStatus(`Case proposal recorded · ${receipt.id}`);
              })}
            >
              <label>
                Measured failed check
                <select name="failure" required>
                  {findings.map(({ result, caseId, check }) => (
                    <option
                      key={`${result.id}:${check}`}
                      value={`${result.id}:${check}`}
                    >
                      {caseId} · {check}
                    </option>
                  ))}
                </select>
              </label>
              <button type="submit" disabled={busy}>
                Propose regression case
              </button>
            </form>
          )}
        </section>
      )}

      <section
        className={css.card}
        aria-label="Inspect exact regression version"
      >
        <h2>Inspect exact regression version</h2>
        <form
          className={formCss.form}
          aria-label="Load regression version"
          onSubmit={submit(async (data) => {
            setVersion(null);
            setVersion(
              await regressionApi.version(
                field(data, "kind") as VersionKind,
                field(data, "version_id"),
              ),
            );
          })}
        >
          <label>
            Version kind{" "}
            <select name="kind">
              <option value="cases">Case</option>
              <option value="suites">Suite</option>
            </select>
          </label>
          <label>
            Exact version ID <input name="version_id" required />
          </label>
          <button type="submit" disabled={busy}>
            Load regression version
          </button>
        </form>
        {version && (
          <>
            <RegressionVersionDetails version={version} />
            <VersionActions
              key={`${version.id}:${version.status}`}
              version={version}
              busy={busy}
              apply={(action, reviewReference) =>
                void perform(async () => {
                  const receipt = await regressionApi.transition(
                    version.id,
                    action,
                    {
                      expected_checksum: version.checksum,
                      review_reference: reviewReference,
                    },
                  );
                  setVersion(receipt);
                  setStatus(`Version ${receipt.status} · ${receipt.id}`);
                })
              }
            />
          </>
        )}
      </section>

      {canManage && (
        <section className={css.card} aria-label="Create regression suite">
          <h2>Create regression suite</h2>
          <form
            className={formCss.form}
            aria-label="Draft regression suite"
            onSubmit={submit(async (data) => {
              const casePins = Array.from({ length: caseCount }, (_, index) =>
                exactPin(
                  field(data, `case_${index}_id`),
                  field(data, `case_${index}_checksum`),
                ),
              );
              const body = {
                code: field(data, "code"),
                case_pins: casePins,
                predecessor: null,
              };
              const receipt = await regressionApi.createSuite({
                ...body,
                operation_id: operation("suite", body),
              });
              setVersion(receipt);
              setStatus(`Suite draft recorded · ${receipt.id}`);
            })}
          >
            <label>
              Suite code <input name="code" required maxLength={120} />
            </label>
            {Array.from({ length: caseCount }, (_, index) => (
              <fieldset key={index}>
                <legend>Published case {index + 1}</legend>
                <PinFields
                  name={`case_${index}`}
                  label={`Published case ${index + 1}`}
                />
              </fieldset>
            ))}
            <button
              type="button"
              disabled={busy || caseCount >= 100}
              onClick={() => setCaseCount((value) => value + 1)}
            >
              Add case pin
            </button>
            {caseCount > 1 && (
              <button
                type="button"
                disabled={busy}
                onClick={() => setCaseCount((value) => value - 1)}
              >
                Remove last case pin
              </button>
            )}
            <button type="submit" disabled={busy}>
              Draft regression suite
            </button>
          </form>
        </section>
      )}

      {canManage && (
        <section
          className={css.card}
          aria-label="Execute regression comparison"
        >
          <h2>Execute regression comparison</h2>
          <form
            className={formCss.form}
            aria-label="Compare pinned runs"
            onSubmit={submit(async (data) => {
              const baselineAdditional = additionalRuns(data, "baseline");
              const candidateAdditional = additionalRuns(data, "candidate");
              const body = {
                suite_version: exactPin(
                  field(data, "suite_id"),
                  field(data, "suite_checksum"),
                ),
                baseline_run: exactPin(
                  field(data, "baseline_id"),
                  field(data, "baseline_checksum"),
                ),
                candidate_run: exactPin(
                  field(data, "candidate_id"),
                  field(data, "candidate_checksum"),
                ),
                ...(baselineAdditional.length > 0 && {
                  baseline_additional_runs: baselineAdditional,
                }),
                ...(candidateAdditional.length > 0 && {
                  candidate_additional_runs: candidateAdditional,
                }),
                dimension: field(data, "dimension") as ComparisonDimension,
              };
              const receipt = await regressionApi.compare({
                ...body,
                operation_id: operation("compare", body),
              });
              setStatus(`Comparison recorded · ${receipt.run.id}`);
              setComparison(receipt);
            })}
          >
            <PinFields name="suite" label="Published suite version" />
            <PinFields name="baseline" label="Baseline run" />
            <PinFields name="candidate" label="Candidate run" />
            <details>
              <summary>Additional pinned runs</summary>
              <p>
                Include exact run receipts when a suite draws cases from
                multiple existing runs. All components are verified against the
                selected suite.
              </p>
              <AdditionalRunFields side="baseline" busy={busy} />
              <AdditionalRunFields side="candidate" busy={busy} />
            </details>
            <label>
              Comparison dimension{" "}
              <select name="dimension">
                {(
                  [
                    "TECHNICAL_FIXTURE",
                    "EXECUTION_REPEAT",
                    "MODEL",
                    "PROMPT",
                    "RUBRIC",
                    "WORKFLOW",
                    "RETRIEVAL",
                    "TRANSCRIPTION",
                  ] as const
                ).map((dimension) => (
                  <option key={dimension} value={dimension}>
                    {dimension === "EXECUTION_REPEAT"
                      ? "Existing execution repeatability"
                      : dimension}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" disabled={busy}>
              Compare pinned runs
            </button>
          </form>
        </section>
      )}

      <section className={css.card} aria-label="Review exact comparison">
        <h2>Review exact comparison</h2>
        <form
          className={formCss.form}
          aria-label="Load regression comparison"
          onSubmit={submit(async (data) => {
            setComparison(null);
            setComparison(
              await regressionApi.comparison(field(data, "comparison_id")),
            );
          })}
        >
          <label>
            Comparison run ID <input name="comparison_id" required />
          </label>
          <button type="submit" disabled={busy}>
            Load regression comparison
          </button>
        </form>
        {comparison && (
          <>
            <p>Human evidence review · {comparison.run.review_status}</p>
            <p>Release recommendation · {comparison.release_recommendation}</p>
            <RegressionComparisonDetails report={comparison.regression} />
            {comparison.reviews.length > 0 && (
              <MetadataTable
                caption="Recorded evidence recommendations"
                columns={[
                  ["id", "Review receipt"],
                  ["decision", "Evidence recommendation"],
                  ["reviewer_user_id", "Reviewer"],
                  ["created_at", "Recorded at"],
                ]}
                rows={comparison.reviews}
              />
            )}
            {comparison.run.checksum &&
              comparison.allowed_actions.includes("review") && (
                <form
                  className={formCss.form}
                  aria-label="Record evidence recommendation"
                  onSubmit={submit(async (data) => {
                    if (!comparison.run.checksum)
                      throw new Error("Load an exact comparison checksum.");
                    const body = {
                      expected_checksum: comparison.run.checksum,
                      decision: field(data, "decision") as EvidenceDecision,
                      review_reference: field(data, "review_reference"),
                    };
                    const receipt = await regressionApi.review(
                      comparison.run.id,
                      {
                        ...body,
                        operation_id: operation(
                          `review:${comparison.run.id}`,
                          body,
                        ),
                      },
                    );
                    setStatus(
                      `Evidence recommendation recorded · ${receipt.id}`,
                    );
                    setComparison(
                      await regressionApi.comparison(comparison.run.id),
                    );
                  })}
                >
                  <EvidenceRecommendationSelect
                    hardGate={comparison.run.hard_gate}
                  />
                  <label>
                    Comparison review reference{" "}
                    <input name="review_reference" required maxLength={200} />
                  </label>
                  <p>
                    An evidence recommendation records a human decision about
                    this comparison. Empirical readiness and production
                    authorization have separate gates.
                  </p>
                  <button type="submit" disabled={busy}>
                    Record evidence recommendation
                  </button>
                </form>
              )}
          </>
        )}
      </section>
    </>
  );
}

export default function RegressionWorkbench() {
  const { user } = useAuth();
  const permissions = user?.platform_permissions ?? [];
  const read =
    permissions.includes("platform.control_center.read") &&
    permissions.includes("standards.evaluation.read");
  const manage =
    read &&
    permissions.includes("ai.manage") &&
    permissions.includes("standards.evaluation.run");
  const review =
    read &&
    permissions.includes("ai.manage") &&
    permissions.includes("ai.review");
  return (
    <>
      <h1>Regression workbench</h1>
      <p>
        <Link href="/control-center/evaluation">Return to Evaluation</Link>
      </p>
      {manage || review ? (
        <Workbench key={user?.id} canManage={manage} />
      ) : (
        <p className={css.error} role="alert">
          Your platform permissions do not allow regression governance.
        </p>
      )}
    </>
  );
}
