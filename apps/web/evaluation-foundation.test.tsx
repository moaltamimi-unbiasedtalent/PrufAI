import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DemoUser } from "./lib/access";
import {
  evaluationApi,
  type EvaluationDataset,
  type EvaluationMetric,
  type EvaluationRun,
  type EvaluationRunDetail,
} from "./app/control-center/evaluation/evaluation-api";
import EvaluationPageView, {
  EvaluationDatasetTable,
  EvaluationMetricsTable,
  EvaluationRunDetails,
  EvaluationRunTable,
} from "./app/control-center/evaluation/evaluation-view";
import { controlLinks } from "./app/control-center/shell";

const operator: DemoUser = {
  id: "platform-operator",
  email: "operator@example.test",
  display_name: "Platform operator",
  memberships: [],
  platform_scope: true,
  platform_permissions: [
    "platform.control_center.read",
    "standards.evaluation.read",
  ],
};
let user = operator;
vi.mock("./lib/auth", () => ({
  useAuth: () => ({ user, loading: false, membership: user.memberships[0] }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/control-center/evaluation",
  useRouter: () => ({ replace: vi.fn() }),
}));

const id = "b8a1ad1f-69c2-4f73-837c-89915924fcb0";
const digest = "a".repeat(64);
const run: EvaluationRun = {
  id,
  dataset_version_id: "12631f7d-31b9-4984-b034-6a5ba125d552",
  dataset_checksum: digest,
  status: "COMPLETED",
  hard_gate: "PASS",
  execution_class: "TEST_ONLY",
  evidence_class: "SYNTHETIC_REGRESSION",
  case_count: 2,
  failure_count: 0,
  created_at: "2026-11-04T12:00:00Z",
  completed_at: "2026-11-04T12:00:01Z",
  review_status: "PENDING",
};
const dataset: EvaluationDataset = {
  id: run.dataset_version_id,
  dataset_id: "21bb7d3d-b609-4ff4-a7b6-59588e94b97a",
  code: "ORIGINAL_SYNTHETIC_DATASET",
  version_number: 1,
  status: "DRAFT",
  assessment_family: "INTEGRATION_KNOWLEDGE",
  language: "de",
  cefr_level: null,
  skill: "RETRIEVAL",
  evidence_class: "SYNTHETIC_REGRESSION",
  checksum: digest,
  case_count: 2,
  approval_status: "NOT_APPROVED",
  created_at: run.created_at,
  approved_at: null,
  published_at: null,
};
const metric: EvaluationMetric = {
  name: "schema_compliance",
  value: 0.5,
  numerator: 1,
  denominator: 2,
  sample_size: 2,
  missing_count: 0,
  evidence_class: "TECHNICAL_TEST",
  unavailable_reason: null,
  limitations: ["Synthetic technical evidence only"],
};
function detail(): EvaluationRunDetail {
  return {
    run,
    pins: {
      dataset_version_id: run.dataset_version_id,
      dataset_checksum: digest,
      dataset_split: "TEST",
      standard_pack_version_id: "9c248224-4d0f-484b-b3e3-e67e63d1268a",
      standard_pack_checksum: digest,
      rubric_version_id: null,
      rubric_checksum: null,
      question_bank_item_version_id: null,
      item_checksum: null,
      model_profile_id: null,
      model_version: null,
      prompt_version_id: null,
      agent_version_id: null,
      workflow_version_id: null,
      profile_version: null,
      execution_adapter: "DETERMINISTIC_TEST",
      evaluation_method: "governed-evaluation",
      evaluation_method_version: "1",
      metric_schema_version: "1",
      metric_normalization: "NFC_WHITESPACE",
      metric_retrieval_k: 5,
      metric_configuration_checksum: digest,
    },
    metrics: [metric],
    failures: [
      { case_id: "negative-case", failure_categories: ["SCHEMA_INVALID"] },
    ],
    reviews: [],
    limitations: ["No expert-labelled assessment validity is established"],
  };
}

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });

beforeEach(() => {
  user = operator;
});
afterEach(async () => {
  const api = await import("./lib/api");
  api.setSessionToken(null);
  vi.unstubAllGlobals();
});

describe("governed Evaluation workspace", () => {
  it("requires the existing explicit platform evaluation permission", () => {
    expect(controlLinks).toContainEqual([
      "Evaluation",
      "/control-center/evaluation",
      "standards.evaluation.read",
    ]);
    const loading = renderToStaticMarkup(<EvaluationPageView />);
    expect(loading).toContain("Loading dataset versions");
    expect(loading).toContain("Technical correctness is separate");
    user = {
      ...operator,
      platform_permissions: ["platform.control_center.read"],
    };
    const denied = renderToStaticMarkup(<EvaluationPageView />);
    expect(denied).toContain(
      "Your platform permissions do not allow evaluation metadata",
    );
    expect(denied).not.toContain("Loading dataset versions");
  });

  it("does not borrow institutional evaluation permissions or grant entry permission", () => {
    user = {
      ...operator,
      platform_permissions: [],
      memberships: [
        {
          organisation_id: "institution-a",
          organisation_name: "Institution A",
          status: "ACTIVE",
          role_keys: ["INSTITUTION_ADMIN"],
          permissions: [
            "platform.control_center.read",
            "standards.evaluation.read",
          ],
        },
      ],
    };
    expect(renderToStaticMarkup(<EvaluationPageView />)).toContain(
      "Your platform permissions do not allow",
    );
    user = { ...operator, platform_permissions: ["standards.evaluation.read"] };
    expect(renderToStaticMarkup(<EvaluationPageView />)).toContain(
      "Your platform permissions do not allow",
    );
  });

  it("shows exact DRAFT provenance without inventing CEFR, approval or private evidence", () => {
    const html = renderToStaticMarkup(
      <EvaluationDatasetTable
        items={[
          {
            ...dataset,
            private_response: "PRIVATE RESPONSE",
            expert_notes: "PRIVATE EXPERT NOTE",
          } as EvaluationDataset,
        ]}
      />,
    );
    expect(html).toContain("ORIGINAL_SYNTHETIC_DATASET");
    expect(html).toContain("DRAFT");
    expect(html).toContain("NOT_APPROVED");
    expect(html).toContain("INTEGRATION_KNOWLEDGE");
    expect(html).toContain(digest);
    expect(html).not.toContain("B1");
    expect(html).not.toContain("PRIVATE RESPONSE");
    expect(html).not.toContain("PRIVATE EXPERT NOTE");
  });

  it("renders literal measured values with valid denominators and missing observations", () => {
    const html = renderToStaticMarkup(
      <EvaluationMetricsTable metrics={[metric]} />,
    );
    expect(html).toContain("0.5");
    expect(html).toContain("1 / 2");
    expect(html).toContain("Sample size");
    expect(html).toContain("Missing observations");
    expect(html).toContain("TECHNICAL_TEST");
    expect(html).not.toContain("50%");
  });

  it("does not manufacture a zero or percentage from missing reference data", () => {
    const html = renderToStaticMarkup(
      <EvaluationMetricsTable
        metrics={[
          {
            ...metric,
            name: "word_error_rate",
            value: null,
            numerator: 0,
            denominator: 0,
            sample_size: 0,
            missing_count: 2,
            unavailable_reason: "NO_REFERENCE_LABELS",
          },
        ]}
      />,
    );
    expect(html).toContain("Not available");
    expect(html).toContain("NO_REFERENCE_LABELS");
    expect(html).not.toContain("0 / 0");
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("%");
  });

  it("keeps TEST ONLY and pending review visible without activation controls", () => {
    const html = renderToStaticMarkup(
      <EvaluationRunTable items={[run]} inspect={() => undefined} />,
    );
    expect(html).toContain("TEST_ONLY");
    expect(html).toContain("PENDING");
    expect(html).toContain(`Inspect evaluation run ${id}`);
    expect(html).not.toContain("Approve");
    expect(html).not.toContain("Publish");
    expect(html).not.toContain("Activate");
  });

  it("projects exact pins, failed-case references and review status without raw configuration or notes", () => {
    const proof = detail();
    const unexpectedFields = {
      ...proof,
      private_audio: "PRIVATE AUDIO",
      raw_transcript: "PRIVATE TRANSCRIPT",
      provider_credentials: "PRIVATE KEY",
      expert_notes: "PRIVATE EXPERT NOTES",
      pins: { ...proof.pins, configuration: "PRIVATE CONFIGURATION" },
      failures: [{ ...proof.failures[0], response: "PRIVATE RESPONSE" }],
    };
    const html = renderToStaticMarkup(
      <EvaluationRunDetails detail={unexpectedFields} />,
    );
    expect(html).toContain("Metric configuration checksum");
    expect(html).toContain("Selected dataset split");
    expect(html).toContain("NFC_WHITESPACE");
    expect(html).toContain("Retrieval relevance cutoff (k)");
    expect(html).toContain("DETERMINISTIC_TEST");
    expect(html).toContain("negative-case");
    expect(html).toContain("SCHEMA_INVALID");
    expect(html).toContain("No human review decision is recorded");
    for (const privateText of [
      "PRIVATE AUDIO",
      "PRIVATE TRANSCRIPT",
      "PRIVATE KEY",
      "PRIVATE EXPERT NOTES",
      "PRIVATE CONFIGURATION",
      "PRIVATE RESPONSE",
    ])
      expect(html).not.toContain(privateText);
  });

  it("renders measured failure values and recorded review as evidence rather than automatic activation", () => {
    const proof = detail();
    const html = renderToStaticMarkup(
      <EvaluationRunDetails
        detail={{
          ...proof,
          run: {
            ...run,
            hard_gate: "FAIL",
            failure_count: 1,
            review_status: "CHANGES_REQUIRED",
          },
          reviews: [
            {
              id: "review-a",
              decision: "CHANGES_REQUIRED",
              reviewer_user_id: "reviewer-b",
              created_at: run.completed_at,
            },
          ],
        }}
      />,
    );
    expect(html).toContain("Technical hard gate: FAIL");
    expect(html).toContain("CHANGES_REQUIRED");
    expect(html).toContain("reviewer-b");
    expect(html).not.toContain("<button");
  });
});

describe("read-only Evaluation API boundary", () => {
  it("uses the Control Centre endpoint with pagination and no selected tenant header", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("platform-token");
    api.setOrganisationContext("selected-institution");
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(json({ items: [], total: 0, offset: 20, limit: 20 }));
    vi.stubGlobal("fetch", fetch);
    await evaluationApi.datasets(20);
    const [path, init] = fetch.mock.calls[0];
    const url = new URL(String(path), "http://localhost");
    expect(url.pathname).toBe("/api/v1/platform/control-center/evaluation");
    expect(url.searchParams.get("kind")).toBe("datasets");
    expect(url.searchParams.get("offset")).toBe("20");
    expect(url.searchParams.get("limit")).toBe("20");
    const headers = new Headers(init?.headers);
    expect(headers.has("X-PrufAI-Organisation-ID")).toBe(false);
    expect(headers.get("Authorization")).toBe("Bearer platform-token");
    expect(init?.method).toBeUndefined();
  });

  it("reads exact run details and rejects arbitrary paths before a request", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(json(detail()));
    vi.stubGlobal("fetch", fetch);
    await evaluationApi.run(id);
    expect(fetch.mock.calls[0][0]).toBe(
      `/api/v1/platform/control-center/evaluation/runs/${id}`,
    );
    expect(() => evaluationApi.run("../private-evidence")).toThrow(
      "exact evaluation run reference",
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("returns safe permission errors instead of private backend details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ detail: "PRIVATE EXPERT NOTES" }, 403)),
    );
    await expect(evaluationApi.runs()).rejects.toMatchObject({
      status: 403,
      message: "Your platform permissions do not allow this view.",
    });
  });
});
