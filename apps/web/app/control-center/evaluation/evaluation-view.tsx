"use client";

import React, { useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import { displayMetadata } from "../control-api";
import css from "../control-center.module.css";
import { ErrorNotice, MetadataTable, Pagination } from "../metadata";
import ReferenceSummaryView from "./references/reference-summary";
import {
  evaluationApi,
  type EvaluationDataset,
  type EvaluationMetric,
  type EvaluationPage,
  type EvaluationPins,
  type EvaluationRun,
  type EvaluationRunDetail,
} from "./evaluation-api";

type View = "datasets" | "runs" | "references";
const evidenceNotice =
  "Technical correctness is separate from German language assessment validity. Synthetic or TEST ONLY results do not establish CEFR validity, calibrated accuracy, fairness or production approval.";

export function EvaluationDatasetTable({
  items,
}: {
  items: EvaluationDataset[];
}) {
  return (
    <MetadataTable
      caption="Dataset versions"
      columns={[
        ["code", "Dataset"],
        ["version_number", "Version"],
        ["status", "Lifecycle status"],
        ["approval_status", "Approval status"],
        ["evidence_class", "Evidence class"],
        ["case_count", "Cases"],
        ["assessment_family", "Assessment family"],
        ["language", "Language"],
        ["cefr_level", "CEFR where applicable"],
        ["skill", "Skill"],
        ["id", "Exact version reference"],
        ["dataset_id", "Dataset reference"],
        ["checksum", "Version checksum"],
        ["created_at", "Created"],
        ["approved_at", "Approved"],
        ["published_at", "Published"],
      ]}
      rows={items}
    />
  );
}

export function EvaluationRunTable({
  items,
  inspect,
}: {
  items: EvaluationRun[];
  inspect: (id: string) => void;
}) {
  return (
    <div
      className={css.tableRegion}
      role="region"
      aria-label="Evaluation runs scrollable table"
      tabIndex={0}
    >
      <table className={css.table}>
        <caption>Evaluation runs</caption>
        <thead>
          <tr>
            {[
              "Run reference",
              "Status",
              "Technical hard gate",
              "Execution class",
              "Evidence class",
              "Cases",
              "Failed cases",
              "Human review",
              "Exact dataset version",
              "Completed",
            ].map((label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((run) => (
            <tr key={run.id}>
              <td>
                {run.id}
                <p>
                  <button
                    className={css.secondary}
                    onClick={() => inspect(run.id)}
                    aria-label={`Inspect evaluation run ${run.id}`}
                  >
                    Inspect run
                  </button>
                </p>
              </td>
              <td>{run.status}</td>
              <td>{run.hard_gate}</td>
              <td>{run.execution_class}</td>
              <td>{run.evidence_class}</td>
              <td>{run.case_count}</td>
              <td>{run.failure_count}</td>
              <td>{run.review_status}</td>
              <td>
                {run.dataset_version_id}
                <br />
                {run.dataset_checksum}
              </td>
              <td>{run.completed_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const pinLabels: readonly [keyof EvaluationPins, string][] = [
  ["dataset_version_id", "Dataset version"],
  ["dataset_checksum", "Dataset checksum"],
  ["dataset_split", "Selected dataset split"],
  ["standard_pack_version_id", "Standard Pack version"],
  ["standard_pack_checksum", "Standard Pack checksum"],
  ["rubric_version_id", "Rubric version"],
  ["rubric_checksum", "Rubric checksum"],
  ["question_bank_item_version_id", "Question/item version"],
  ["item_checksum", "Item checksum"],
  ["model_profile_id", "Model profile"],
  ["model_version", "Model version"],
  ["prompt_version_id", "Prompt version"],
  ["agent_version_id", "Agent version"],
  ["workflow_version_id", "Workflow version"],
  ["profile_version", "Execution profile fingerprint"],
  ["execution_adapter", "Execution adapter"],
  ["evaluation_method", "Evaluation method"],
  ["evaluation_method_version", "Evaluation method version"],
  ["metric_schema_version", "Metric configuration schema version"],
  ["metric_normalization", "Metric text normalization"],
  ["metric_retrieval_k", "Retrieval relevance cutoff (k)"],
  ["metric_configuration_checksum", "Metric configuration checksum"],
];

function metricValue(value: number | null) {
  return value !== null && Number.isFinite(value)
    ? String(value)
    : "Not available";
}

export function EvaluationMetricsTable({
  metrics,
}: {
  metrics: EvaluationMetric[];
}) {
  if (!metrics.length)
    return (
      <p className={css.notice}>
        No measured metrics are recorded for this run.
      </p>
    );
  return (
    <div
      className={css.tableRegion}
      role="region"
      aria-label="Measured evaluation metrics scrollable table"
      tabIndex={0}
    >
      <table className={css.table}>
        <caption>Measured evaluation metrics</caption>
        <thead>
          <tr>
            {[
              "Metric",
              "Reported value",
              "Numerator / denominator",
              "Sample size",
              "Missing observations",
              "Evidence class",
              "Availability and limitations",
            ].map((label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => (
            <tr key={metric.name}>
              <td>{metric.name}</td>
              <td>{metricValue(metric.value)}</td>
              <td>
                {metric.numerator !== null &&
                metric.denominator !== null &&
                metric.denominator > 0
                  ? `${metric.numerator} / ${metric.denominator}`
                  : "Not available"}
              </td>
              <td>{metric.sample_size}</td>
              <td>{metric.missing_count}</td>
              <td>{metric.evidence_class}</td>
              <td>
                {metric.unavailable_reason ??
                  (metric.value === null
                    ? "No measurement available"
                    : "Recorded measurement")}
                {metric.limitations.length > 0 && (
                  <ul>
                    {metric.limitations.map((limitation, index) => (
                      <li key={index}>{limitation}</li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EvaluationRunDetails({
  detail,
}: {
  detail: EvaluationRunDetail;
}) {
  return (
    <section aria-label="Evaluation run details">
      <h2>Evaluation run details</h2>
      <p>Run reference: {detail.run.id}</p>
      {detail.run.checksum && <p>Run checksum: {detail.run.checksum}</p>}
      <p>
        Technical hard gate: {detail.run.hard_gate}. Execution class:{" "}
        {detail.run.execution_class}. Evidence class:{" "}
        {detail.run.evidence_class}. Human review: {detail.run.review_status}.
      </p>
      <p className={css.notice}>{evidenceNotice}</p>
      <h3>Exact pinned configuration</h3>
      <dl className={css.health}>
        {pinLabels.map(([key, label]) => (
          <React.Fragment key={key}>
            <dt>{label}</dt>
            <dd>{displayMetadata(detail.pins[key])}</dd>
          </React.Fragment>
        ))}
      </dl>
      {detail.comparison ? (
        <section aria-label="Exact reference calibration comparison">
          <h3>Exact reference calibration comparison</h3>
          <p>Comparison: {detail.comparison.comparison_kind}</p>
          <dl className={css.health}>
            {(["left", "right"] as const).map((side) => (
              <React.Fragment key={side}>
                <dt>{side === "left" ? "Left" : "Right"} exact version</dt>
                <dd>
                  {detail.comparison?.[side].kind} ·{" "}
                  {detail.comparison?.[side].version_id} ·{" "}
                  {detail.comparison?.[side].checksum} ·{" "}
                  {detail.comparison?.[side].execution_class}
                </dd>
              </React.Fragment>
            ))}
          </dl>
          <p className={css.notice}>
            Quality claim: {detail.comparison.quality_claim}. Empirical
            thresholds: {detail.comparison.empirical_thresholds}. Production
            activation:{" "}
            {detail.comparison.production_activation
              ? "Unavailable configuration"
              : "Not authorized"}
            .
          </p>
          <h4>Measured comparison coverage</h4>
          {detail.comparison.criteria.length ? (
            <MetadataTable
              caption="Comparison criterion coverage"
              columns={[
                ["criterion_code", "Criterion"],
                ["expected_count", "Expected paired observations"],
                ["sample_size", "Measured paired observations"],
                [
                  "disagreement_distribution",
                  "Observed signed difference distribution",
                ],
              ]}
              rows={detail.comparison.criteria.map((criterion) => ({
                ...criterion,
                disagreement_distribution:
                  criterion.disagreement_distribution.map(
                    ({ difference, absolute_difference, count, rate }) =>
                      `Signed difference ${difference}; absolute difference ${absolute_difference}; count ${count}; rate ${rate}`,
                  ),
              }))}
            />
          ) : (
            <p>No criterion-level comparison observations are available.</p>
          )}
          <h4>Uncertainty and assumptions</h4>
          <dl className={css.health}>
            {Object.entries(detail.comparison.uncertainty).map(
              ([key, value]) => (
                <React.Fragment key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>
                    {typeof value === "string" ||
                    typeof value === "number" ||
                    typeof value === "boolean" ||
                    value === null
                      ? displayMetadata(value)
                      : JSON.stringify(value)}
                  </dd>
                </React.Fragment>
              ),
            )}
          </dl>
          <ul>
            {detail.comparison.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <EvaluationMetricsTable metrics={detail.metrics} />
      <h3>Failed-case references</h3>
      {detail.failures.length ? (
        <MetadataTable
          caption="Failed cases"
          columns={[
            ["case_id", "Case reference"],
            ["failure_categories", "Failure categories"],
          ]}
          rows={detail.failures}
        />
      ) : (
        <p>No failed-case references are recorded.</p>
      )}
      <h3>Human quality review</h3>
      {detail.reviews.length ? (
        <MetadataTable
          caption="Recorded review decisions"
          columns={[
            ["id", "Review reference"],
            ["decision", "Decision"],
            ["reviewer_user_id", "Reviewer reference"],
            ["created_at", "Recorded"],
          ]}
          rows={detail.reviews}
        />
      ) : (
        <p>
          No human review decision is recorded. Execution does not approve a run
          or activate a production threshold.
        </p>
      )}
      <h3>Evidence limitations</h3>
      {detail.limitations.length ? (
        <ul>
          {detail.limitations.map((limitation, index) => (
            <li key={index}>{limitation}</li>
          ))}
        </ul>
      ) : (
        <p>
          No additional limitations are recorded. This does not establish pilot
          assessment validity.
        </p>
      )}
    </section>
  );
}

export default function EvaluationPageView() {
  const { user } = useAuth();
  const allowed = Boolean(
    user?.platform_permissions.includes("platform.control_center.read") &&
      user.platform_permissions.includes("standards.evaluation.read"),
  );
  const [view, setView] = useState<View>("datasets");
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [datasets, setDatasets] =
    useState<EvaluationPage<EvaluationDataset> | null>(null);
  const [runs, setRuns] = useState<EvaluationPage<EvaluationRun> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [detail, setDetail] = useState<EvaluationRunDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailReload, setDetailReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setDatasets(null);
    setRuns(null);
    setError(null);
    if (!allowed || view === "references") return;
    const request =
      view === "datasets"
        ? evaluationApi.datasets(offset)
        : evaluationApi.runs(offset);
    void request
      .then((value) => {
        if (cancelled) return;
        if (view === "datasets")
          setDatasets(value as EvaluationPage<EvaluationDataset>);
        else setRuns(value as EvaluationPage<EvaluationRun>);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Evaluation metadata could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, view, offset, reload, user?.id]);
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    if (!allowed || !selectedRun || view !== "runs") return;
    void evaluationApi
      .run(selectedRun)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setDetailError(
            cause instanceof Error
              ? cause.message
              : "Run details could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, selectedRun, view, detailReload, user?.id]);
  const page = view === "datasets" ? datasets : runs;
  return (
    <>
      <p className={css.eyebrow}>Read-only platform governance</p>
      <h1>Evaluation</h1>
      <p className={css.intro}>
        Inspect governed dataset versions, exact evaluation configuration,
        measured outcomes and human quality-review status. Private dataset
        evidence and expert notes are not exposed here.
      </p>
      <p className={css.notice}>{evidenceNotice}</p>
      {!allowed ? (
        <p className={css.error} role="alert">
          Your platform permissions do not allow evaluation metadata.
        </p>
      ) : (
        <>
          <label className={css.filters}>
            Evaluation view{" "}
            <select
              value={view}
              onChange={(event) => {
                setView(event.target.value as View);
                setOffset(0);
                setSelectedRun(null);
              }}
            >
              <option value="datasets">Dataset versions</option>
              <option value="runs">Evaluation runs</option>
              <option value="references">Reference governance</option>
            </select>
          </label>
          {view === "references" ? (
            <ReferenceSummaryView key={user?.id} />
          ) : error ? (
            <ErrorNotice
              message={error}
              retry={() => setReload((value) => value + 1)}
            />
          ) : !page ? (
            <p role="status">
              Loading{" "}
              {view === "datasets" ? "dataset versions" : "evaluation runs"}…
            </p>
          ) : (
            <>
              {!page.items.length ? (
                <p className={css.notice}>
                  No{" "}
                  {view === "datasets"
                    ? "governed dataset versions"
                    : "evaluation runs"}{" "}
                  are available in the authorized scope. No data or results are
                  simulated.
                </p>
              ) : view === "datasets" && datasets ? (
                <EvaluationDatasetTable items={datasets.items} />
              ) : runs ? (
                <EvaluationRunTable
                  items={runs.items}
                  inspect={setSelectedRun}
                />
              ) : null}
              <Pagination
                page={{
                  ...page,
                  items: page.items.map((item) => ({ id: item.id })),
                }}
                setOffset={(value) => {
                  setOffset(value);
                  setSelectedRun(null);
                }}
              />
            </>
          )}
          {selectedRun &&
            view === "runs" &&
            (detailError ? (
              <ErrorNotice
                message={detailError}
                retry={() => setDetailReload((value) => value + 1)}
              />
            ) : detail ? (
              <EvaluationRunDetails detail={detail} />
            ) : (
              <p role="status">Loading exact evaluation run…</p>
            ))}
        </>
      )}
    </>
  );
}
