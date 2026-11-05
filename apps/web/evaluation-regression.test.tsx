import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import type { RegressionReport } from "./app/control-center/evaluation/regressions/regression-api";
import { RegressionComparisonDetails } from "./app/control-center/evaluation/regressions/regression-details";
import RegressionWorkbench, {
  additionalRuns,
  EvidenceRecommendationSelect,
} from "./app/control-center/evaluation/regressions/regression-workbench";

vi.mock("./lib/auth", () => ({
  useAuth: () => ({
    user: {
      id: "observer",
      platform_permissions: [
        "platform.control_center.read",
        "standards.evaluation.read",
      ],
    },
  }),
}));

it("shows missing and incompatible evidence without substituting a passing value", () => {
  const pin = {
    id: "e64819b1-951b-4663-90ac-a41b8473c912",
    checksum: "a".repeat(64),
  };
  const additional = {
    id: "7ee9cf4a-be0e-43a1-9f7c-d3309e33ecfa",
    checksum: "b".repeat(64),
  };
  const report: RegressionReport = {
    suite_version: pin,
    baseline_run: pin,
    candidate_run: pin,
    baseline_components: [
      {
        run: pin,
        dataset_version: pin,
        configuration_checksum: "c".repeat(64),
        case_count: 1,
      },
      {
        run: additional,
        dataset_version: pin,
        configuration_checksum: "d".repeat(64),
        case_count: 1,
      },
    ],
    baseline_dataset_version: pin,
    candidate_dataset_version: pin,
    dataset_split: "TEST",
    dimension: "RUBRIC",
    comparison_status: "NOT_COMPARABLE",
    comparison_reason_code: "RUBRIC_SCOPE_MISMATCH",
    technical_status: "NOT_EVALUATED",
    empirical_status: "NOT_EVALUATED",
    decision: "NOT_COMPARABLE",
    expected_case_count: 2,
    paired_case_count: 0,
    missing_baseline_case_ids: [],
    missing_candidate_case_ids: ["missing-case"],
    case_findings: [],
    paired_metrics: [
      {
        name: "criterion_coverage_rate",
        baseline: {
          value: 0,
          numerator: "0.0000",
          denominator: "1.0000",
          sample_size: 1,
          missing_count: 0,
        },
        candidate: null,
        delta: null,
        paired_case_count: 0,
        missing_case_count: 1,
        evidence_class: "TECHNICAL_TEST",
      },
    ],
    changed_pins: [],
    baseline_artifacts: {},
    candidate_artifacts: {},
    empirical: { reason_code: "TECHNICAL_OR_COMPARISON_EVIDENCE_INELIGIBLE" },
    reference_maturity: "TECHNICAL_TEST",
    reference_quality_status: "NOT_AVAILABLE",
    execution_class: "TEST_ONLY",
    limitations: ["Exact reference eligibility is required."],
    production_activation: false,
  };
  const html = renderToStaticMarkup(
    <RegressionComparisonDetails report={report} />,
  );
  expect(html).toContain("NOT_COMPARABLE");
  expect(html).toContain("RUBRIC_SCOPE_MISMATCH");
  expect(html).toContain("NOT_EVALUATED");
  expect(html).toContain("missing-case");
  expect(html).toContain("0.0000 / 1.0000");
  expect(html).toContain("Not available");
  expect(html).toContain("Exact comparison components");
  expect(html).toContain(additional.id);
  expect(html).toContain(additional.checksum);
  expect(html).toContain("d".repeat(64));
  expect(html).not.toContain("<dd>PASS</dd>");
});

it("keeps optional run pins paired and scoped to their comparison side", () => {
  const data = new FormData();
  const first = {
    id: "e64819b1-951b-4663-90ac-a41b8473c912",
    checksum: "a".repeat(64),
  };
  const second = {
    id: "7ee9cf4a-be0e-43a1-9f7c-d3309e33ecfa",
    checksum: "b".repeat(64),
  };
  for (const pin of [first, second]) {
    data.append("baseline_additional_id", pin.id);
    data.append("baseline_additional_checksum", pin.checksum);
  }
  expect(additionalRuns(data, "baseline")).toEqual([first, second]);
  expect(additionalRuns(data, "candidate")).toEqual([]);
  data.append("candidate_additional_id", first.id);
  expect(() => additionalRuns(data, "candidate")).toThrow(
    "exact ID and checksum",
  );
});

it("keeps mutation controls outside the read-only evaluation permission", () => {
  const html = renderToStaticMarkup(<RegressionWorkbench />);
  expect(html).toContain(
    "Your platform permissions do not allow regression governance.",
  );
  expect(html).not.toContain("Compare pinned runs");
  expect(html).not.toContain("Propose regression case");
});

it("offers evidence acceptance only for a passing technical gate", () => {
  const failed = renderToStaticMarkup(
    <EvidenceRecommendationSelect hardGate="FAIL" />,
  );
  expect(failed).not.toContain('value="ACCEPTED"');
  expect(failed).toContain('value="REJECTED"');
  expect(failed).toContain('value="CHANGES_REQUIRED" selected=""');

  const passed = renderToStaticMarkup(
    <EvidenceRecommendationSelect hardGate="PASS" />,
  );
  expect(passed).toContain('value="ACCEPTED" selected=""');
});
