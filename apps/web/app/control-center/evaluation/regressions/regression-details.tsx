import React from "react";
import css from "../../control-center.module.css";
import { MetadataTable } from "../../metadata";
import type {
  Pin,
  RegressionReport,
  RegressionVersion,
} from "./regression-api";

function pinLabel(pin: Pin | null) {
  return pin ? `${pin.id} · ${pin.checksum}` : "Not available";
}

function counts(
  numerator: number | string | null,
  denominator: number | string | null,
) {
  return numerator !== null && denominator !== null && Number(denominator) > 0
    ? `${numerator} / ${denominator}`
    : "Not available";
}

export function RegressionVersionDetails({
  version,
}: {
  version: RegressionVersion;
}) {
  return (
    <section aria-label="Exact regression version">
      <h3>
        {version.code} · version {version.version_number}
      </h3>
      <dl className={css.health}>
        <dt>Version receipt</dt>
        <dd>{pinLabel(version)}</dd>
        <dt>Dataset identity</dt>
        <dd>{version.dataset_id}</dd>
        <dt>Lifecycle status</dt>
        <dd>{version.status}</dd>
        <dt>Scope and split</dt>
        <dd>
          {version.assessment_family} · {version.kind} · {version.split}
        </dd>
        <dt>Purpose</dt>
        <dd>{version.purpose}</dd>
        <dt>Evidence maturity</dt>
        <dd>{version.evidence_maturity}</dd>
        <dt>Case count</dt>
        <dd>{version.case_count}</dd>
        <dt>Required checks</dt>
        <dd>{version.required_checks.join(", ")}</dd>
        <dt>Independent approval</dt>
        <dd>{version.approved_by ?? "Pending"}</dd>
        {version.source_basis && (
          <>
            <dt>Source basis</dt>
            <dd>{version.source_basis}</dd>
          </>
        )}
        {version.source && (
          <>
            <dt>Source case</dt>
            <dd>{version.source.case_id}</dd>
            <dt>Source run receipt</dt>
            <dd>
              {pinLabel({
                id: version.source.run_id,
                checksum: version.source.run_checksum,
              })}
            </dd>
            <dt>Source result receipt</dt>
            <dd>
              {pinLabel({
                id: version.source.result_id,
                checksum: version.source.result_checksum,
              })}
            </dd>
          </>
        )}
      </dl>
      {version.case_pins.length > 0 && (
        <details>
          <summary>Pinned case versions</summary>
          <ul>
            {version.case_pins.map((pin) => (
              <li key={pin.case_version_id}>
                {pin.case_id} ·{" "}
                {pinLabel({ id: pin.case_version_id, checksum: pin.checksum })}{" "}
                · {pin.required_checks.join(", ")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function RegressionComparisonDetails({
  report,
}: {
  report: RegressionReport;
}) {
  const hasAdditionalComponents =
    (report.baseline_components?.length ?? 0) > 1 ||
    (report.candidate_components?.length ?? 0) > 1;
  const checks = report.case_findings.flatMap((finding) =>
    finding.checks.map((check) => ({
      id: `${finding.case_id}:${check.code}`,
      case_id: finding.case_id,
      check: check.code,
      baseline: check.baseline
        ? `${check.baseline.status} · ${counts(check.baseline.numerator, check.baseline.denominator)}${check.baseline.reason_code ? ` · ${check.baseline.reason_code}` : ""}`
        : "Not available",
      candidate: check.candidate
        ? `${check.candidate.status} · ${counts(check.candidate.numerator, check.candidate.denominator)}${check.candidate.reason_code ? ` · ${check.candidate.reason_code}` : ""}`
        : "Not available",
    })),
  );
  return (
    <section aria-label="Regression comparison evidence">
      <h3>Regression comparison evidence</h3>
      <dl className={css.health}>
        <dt>Suite version</dt>
        <dd>{pinLabel(report.suite_version)}</dd>
        <dt>Baseline run</dt>
        <dd>{pinLabel(report.baseline_run)}</dd>
        <dt>Candidate run</dt>
        <dd>{pinLabel(report.candidate_run)}</dd>
        <dt>Baseline dataset version</dt>
        <dd>{pinLabel(report.baseline_dataset_version)}</dd>
        <dt>Candidate dataset version</dt>
        <dd>{pinLabel(report.candidate_dataset_version)}</dd>
        <dt>Dataset split</dt>
        <dd>{report.dataset_split}</dd>
        <dt>Comparison dimension</dt>
        <dd>{report.dimension}</dd>
        <dt>Comparison eligibility</dt>
        <dd>
          {report.comparison_status}
          {report.comparison_reason_code &&
            ` · ${report.comparison_reason_code}`}
        </dd>
        <dt>Candidate technical gate</dt>
        <dd>{report.technical_status}</dd>
        <dt>Empirical gate</dt>
        <dd>
          {report.empirical_status}
          {report.empirical.reason_code && ` · ${report.empirical.reason_code}`}
        </dd>
        <dt>Quality decision</dt>
        <dd>{report.decision}</dd>
        <dt>Paired cases</dt>
        <dd>
          {report.paired_case_count} / {report.expected_case_count}
        </dd>
        <dt>Missing baseline cases</dt>
        <dd>{report.missing_baseline_case_ids.join(", ") || "None"}</dd>
        <dt>Missing candidate cases</dt>
        <dd>{report.missing_candidate_case_ids.join(", ") || "None"}</dd>
        <dt>Reference maturity</dt>
        <dd>{report.reference_maturity}</dd>
        <dt>Reference quality</dt>
        <dd>{report.reference_quality_status}</dd>
        <dt>Execution class</dt>
        <dd>{report.execution_class}</dd>
        <dt>Production activation</dt>
        <dd>{report.production_activation ? "Enabled" : "Disabled"}</dd>
      </dl>
      {hasAdditionalComponents && (
        <details>
          <summary>Exact comparison component receipts</summary>
          <MetadataTable
            caption="Exact comparison components"
            columns={[
              ["side", "Side"],
              ["run", "Run receipt"],
              ["dataset", "Dataset version receipt"],
              ["configuration", "Configuration checksum"],
              ["cases", "Cases"],
            ]}
            rows={(["baseline", "candidate"] as const).flatMap((side) =>
              (report[`${side}_components`] ?? []).map((component) => ({
                id: `${side}:${component.run.id}`,
                side,
                run: pinLabel(component.run),
                dataset: pinLabel(component.dataset_version),
                configuration: component.configuration_checksum,
                cases: component.case_count,
              })),
            )}
          />
        </details>
      )}
      {checks.length > 0 ? (
        <MetadataTable
          caption="Measured regression checks"
          columns={[
            ["case_id", "Case"],
            ["check", "Check"],
            ["baseline", "Baseline"],
            ["candidate", "Candidate"],
          ]}
          rows={checks}
        />
      ) : (
        <p>No paired case measurements are available.</p>
      )}
      {report.paired_metrics.length > 0 && (
        <MetadataTable
          caption="Regression metric comparison"
          columns={[
            ["name", "Metric"],
            ["baseline", "Baseline value and counts"],
            ["candidate", "Candidate value and counts"],
            ["delta", "Difference"],
            ["paired_case_count", "Paired cases"],
            ["missing_case_count", "Missing cases"],
            ["evidence_class", "Evidence class"],
          ]}
          rows={report.paired_metrics.map(
            ({
              name,
              baseline,
              candidate,
              delta,
              paired_case_count,
              missing_case_count,
              evidence_class,
            }) => ({
              name,
              baseline: baseline
                ? `${baseline.value ?? "Not available"} · ${counts(baseline.numerator, baseline.denominator)} · n=${baseline.sample_size} · missing=${baseline.missing_count}`
                : "Not available",
              candidate: candidate
                ? `${candidate.value ?? "Not available"} · ${counts(candidate.numerator, candidate.denominator)} · n=${candidate.sample_size} · missing=${candidate.missing_count}`
                : "Not available",
              delta: delta ?? "Not available",
              paired_case_count,
              missing_case_count,
              evidence_class,
            }),
          )}
        />
      )}
      <details>
        <summary>Exact evaluated artifact pins</summary>
        <MetadataTable
          caption="Baseline and candidate artifact pins"
          columns={[
            ["name", "Artifact pin"],
            ["baseline", "Baseline"],
            ["candidate", "Candidate"],
            ["changed", "Changed"],
          ]}
          rows={Object.entries(report.baseline_artifacts).map(
            ([name, baseline]) => ({
              name,
              baseline: baseline ?? "Not applicable",
              candidate: report.candidate_artifacts[name] ?? "Not applicable",
              changed: report.changed_pins.some((pin) => pin.name === name),
            }),
          )}
        />
      </details>
      {report.case_findings.length > 0 && (
        <details>
          <summary>Exact case and result receipts</summary>
          <ul>
            {report.case_findings.map((finding) => (
              <li key={finding.case_id}>
                {finding.case_id} · version {pinLabel(finding.case_version)}
                <p>
                  Baseline {finding.baseline_status} ·{" "}
                  {pinLabel(finding.baseline_result)}
                  {finding.baseline_failure_categories.length > 0 &&
                    ` · ${finding.baseline_failure_categories.join(", ")}`}
                </p>
                <p>
                  Candidate {finding.candidate_status} ·{" "}
                  {pinLabel(finding.candidate_result)}
                  {finding.candidate_failure_categories.length > 0 &&
                    ` · ${finding.candidate_failure_categories.join(", ")}`}
                </p>
                {hasAdditionalComponents &&
                  (["baseline", "candidate"] as const).map((side) => {
                    const execution = finding[`${side}_execution`];
                    return (
                      execution && (
                        <p key={side}>
                          {side} source run {pinLabel(execution.run)} · dataset{" "}
                          {pinLabel(execution.dataset_version)} · execution
                          receipt {execution.receipt_checksum} · input{" "}
                          {execution.input_checksum} · observation{" "}
                          {execution.observation_checksum}
                        </p>
                      )
                    );
                  })}
              </li>
            ))}
          </ul>
        </details>
      )}
      {report.limitations.length > 0 && (
        <details open>
          <summary>Comparison limitations</summary>
          <ul>
            {report.limitations.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
