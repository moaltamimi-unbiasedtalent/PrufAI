import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvaluationRunDetails } from "./app/control-center/evaluation/evaluation-view";
import type { EvaluationRunDetail } from "./app/control-center/evaluation/evaluation-api";
import { ReferenceTable } from "./app/control-center/evaluation/references/reference-summary";
import ReferenceWorkbenchView, {
  PrivateReferenceEvidence,
  ReferenceAction,
  VisibleReferenceJudgments,
  referenceCriteria,
} from "./app/control-center/evaluation/references/reference-view";
import {
  referenceApi,
  type ReferenceSummary,
  type ReferenceWorkbench,
} from "./app/control-center/evaluation/references/reference-api";

vi.mock("./lib/auth", () => ({
  useAuth: () => ({
    user: { id: "denied", platform_permissions: ["standards.evaluation.read"] },
  }),
}));
const id = "12631f7d-31b9-4984-b034-6a5ba125d552";
const checksum = "a".repeat(64);
const summary: ReferenceSummary = {
  id,
  dataset_id: id,
  checksum,
  kind: "WRITING",
  split: "HELD_OUT",
  evidence_class: "SYNTHETIC_TEST",
  status: "APPROVED",
  judgment_count: 2,
  required_judgment_count: 2,
  adjudication_count: 1,
  adjudication_status: "ADJUDICATED",
  dataset_version_ids: [],
  empirical_thresholds: "UNCONFIGURED",
  production_activation: false,
};
const workbench: ReferenceWorkbench = {
  reference: summary,
  source: null,
  rubric: null,
  qualifications: [],
  judgments: [],
  adjudications: [],
  allowed_actions: [],
};

afterEach(async () => {
  (await import("./lib/api")).setSessionToken(null);
  vi.unstubAllGlobals();
});

describe("expert reference UI preserves evidence boundaries", () => {
  it("renders measured exact-version comparison metadata with its uncertainty and unconfigured gates", () => {
    const detail: EvaluationRunDetail = {
      run: {
        id,
        dataset_version_id: id,
        dataset_checksum: checksum,
        status: "COMPLETED",
        case_count: 1,
        failure_count: 0,
        created_at: "2026-11-05T00:00:00Z",
        completed_at: "2026-11-05T00:00:01Z",
        hard_gate: "PASS",
        execution_class: "TEST_ONLY",
        evidence_class: "SYNTHETIC_TEST",
        review_status: "PENDING",
      },
      pins: {
        dataset_version_id: id,
        dataset_checksum: checksum,
        dataset_split: "CALIBRATION",
        standard_pack_version_id: id,
        standard_pack_checksum: checksum,
        rubric_version_id: id,
        rubric_checksum: checksum,
        question_bank_item_version_id: null,
        item_checksum: null,
        model_profile_id: null,
        model_version: null,
        prompt_version_id: null,
        agent_version_id: null,
        workflow_version_id: null,
        profile_version: null,
        execution_adapter: "HUMAN_REFERENCE_COMPARISON",
        evaluation_method: "independent-reference",
        evaluation_method_version: "1",
        metric_schema_version: "1",
        metric_normalization: "NFC_WHITESPACE",
        metric_retrieval_k: 5,
        metric_configuration_checksum: checksum,
      },
      metrics: [],
      failures: [],
      reviews: [],
      limitations: ["Technical evidence only"],
      comparison: {
        comparison_kind: "HUMAN_A_VS_HUMAN_B",
        left: {
          kind: "JUDGMENT",
          version_id: "exact-left",
          checksum,
          execution_class: "TEST_ONLY",
        },
        right: {
          kind: "JUDGMENT",
          version_id: "exact-right",
          checksum: "b".repeat(64),
          execution_class: "TEST_ONLY",
        },
        empirical_thresholds: "UNCONFIGURED",
        quality_claim: "NOT_ESTABLISHED",
        uncertainty: { method: "NOT_ESTIMATED", reason: "One synthetic case" },
        criteria: [
          {
            criterion_code: "TASK",
            expected_count: 1,
            sample_size: 1,
            disagreement_distribution: [
              {
                difference: "-1.0000",
                absolute_difference: "1.0000",
                count: 1,
                rate: 1,
              },
            ],
          },
        ],
        limitations: ["No expert calibration"],
        production_activation: false,
      },
    };
    const privateExtra = {
      candidate_text: "PRIVATE RAW EVIDENCE",
      private_rationale: "PRIVATE COMPARISON RATIONALE",
    };
    const html = renderToStaticMarkup(
      <EvaluationRunDetails
        detail={{
          ...detail,
          ...privateExtra,
          comparison: { ...detail.comparison!, ...privateExtra },
        }}
      />,
    );
    expect(html).toContain("HUMAN_A_VS_HUMAN_B");
    expect(html).toContain("exact-left");
    expect(html).toContain("exact-right");
    expect(html).toContain("One synthetic case");
    expect(html).toContain(
      "Signed difference -1.0000; absolute difference 1.0000; count 1; rate 1",
    );
    expect(html).not.toContain("[object Object]");
    expect(html).toContain("UNCONFIGURED");
    expect(html).toContain("NOT_ESTABLISHED");
    expect(html).toContain("Not authorized");
    expect(html).not.toContain("PRIVATE RAW EVIDENCE");
    expect(html).not.toContain("PRIVATE COMPARISON RATIONALE");
  });
  it("renders mutation controls only when the server authorizes that exact action", () => {
    const button = <button>Approve exact source provenance</button>;
    expect(
      renderToStaticMarkup(
        <ReferenceAction workbench={workbench} action="APPROVE_SOURCE">
          {button}
        </ReferenceAction>,
      ),
    ).not.toContain("<button");
    expect(
      renderToStaticMarkup(
        <ReferenceAction
          workbench={{ ...workbench, allowed_actions: ["DERIVE_DATASET"] }}
          action="APPROVE_SOURCE"
        >
          {button}
        </ReferenceAction>,
      ),
    ).not.toContain("<button");
    expect(
      renderToStaticMarkup(
        <ReferenceAction
          workbench={{ ...workbench, allowed_actions: ["APPROVE_SOURCE"] }}
          action="APPROVE_SOURCE"
        >
          {button}
        </ReferenceAction>,
      ),
    ).toContain("Approve exact source provenance");
  });
  it("does not render another examiner's judgment before independent submissions are complete", () => {
    const another = {
      id,
      checksum,
      examiner_id: "another",
      created_at: "2026-11-05T00:00:00Z",
      body: {
        criteria: [
          {
            criterion_code: "TASK",
            points: 1,
            level: "CLEAR",
            rationale: "PRIVATE OTHER RATIONALE",
            evidence: [],
          },
        ],
      },
    };
    const hidden = renderToStaticMarkup(
      <VisibleReferenceJudgments
        workbench={{
          ...workbench,
          reference: { ...summary, judgment_count: 1 },
          judgments: [another],
        }}
        examinerId="current"
      />,
    );
    expect(hidden).not.toContain("PRIVATE OTHER RATIONALE");
    expect(hidden).toContain("remain blinded");
    const own = renderToStaticMarkup(
      <VisibleReferenceJudgments
        workbench={{
          ...workbench,
          reference: { ...summary, judgment_count: 1 },
          judgments: [another],
        }}
        examinerId="another"
      />,
    );
    expect(own).toContain("PRIVATE OTHER RATIONALE");
  });
  it("shows actual completeness, split and adjudication without claiming calibration or release", () => {
    const html = renderToStaticMarkup(
      <ReferenceTable
        items={[
          {
            ...summary,
            ...{
              private_rationale: "CONFIDENTIAL",
              candidate_text: "PRIVATE TEXT",
            },
          },
        ]}
      />,
    );
    expect(html).toContain("SYNTHETIC_TEST");
    expect(html).toContain("2 / 2");
    expect(html).toContain("HELD_OUT");
    expect(html).toContain("ADJUDICATED");
    expect(html).toContain(checksum);
    expect(html).toContain("UNCONFIGURED");
    expect(html).toContain("Not authorized");
    expect(html).toContain("do not automatically establish consensus");
    expect(html).not.toContain("CONFIDENTIAL");
    expect(html).not.toContain("PRIVATE TEXT");
  });
  it("hides private evidence and mutation forms without explicit workbench permissions", () => {
    const html = renderToStaticMarkup(<ReferenceWorkbenchView />);
    expect(html).toContain("permissions do not allow");
    expect(html).not.toContain("Load exact reference");
    expect(html).not.toContain("Submit immutable");
    expect(
      renderToStaticMarkup(<PrivateReferenceEvidence workbench={workbench} />),
    ).toContain("Private evidence is unavailable");
  });
  it("shows exact authorized source pins and synthetic classification without inventing audio playback", () => {
    const html = renderToStaticMarkup(
      <PrivateReferenceEvidence
        workbench={{
          ...workbench,
          source: {
            code: "SYNTHETIC",
            origin: "ORIGINAL_SYNTHETIC",
            language: "de",
            purpose: "EVALUATION_ONLY",
            retention_classification: "SYNTHETIC_TEST",
            standard_pack_version_id: id,
            standard_pack_checksum: checksum,
            rubric_version_id: null,
            rubric_checksum: null,
            normalization: "NFC_WHITESPACE",
            input: { text: "Ein eigener synthetischer Text." },
          },
        }}
      />,
    );
    expect(html).toContain("Ein eigener synthetischer Text.");
    expect(html).toContain(checksum);
    expect(html).toContain("no genuine examiner qualification");
    expect(html).not.toContain("<audio");
  });
  it("pins exact quote offsets using Unicode code points and rejects unrelated quotes", () => {
    const criterion = {
      points: "2",
      level: "CLEAR",
      evidence: "Grüße",
      reason: "synthetic:test",
    };
    expect(
      referenceCriteria({ TASK: criterion }, "😀 Grüße an alle")[0].evidence,
    ).toEqual([{ quote: "Grüße", start: 2, end: 7 }]);
    expect(() =>
      referenceCriteria(
        { TASK: { ...criterion, evidence: "not present" } },
        "Ein Text",
      ),
    ).toThrow("exact source quote");
  });
});

describe("reference API preserves authentication and exact operation boundaries", () => {
  it("strips forged/selected tenant headers only for exact platform evaluation paths", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("technical-test-token");
    api.setOrganisationContext("selected-school");
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(
        async () => new Response(JSON.stringify({}), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetch);
    await api.authenticatedFetch(
      `/api/v1/platform/evaluation/references/${id}/workbench`,
      { headers: { "X-PrufAI-Organisation-ID": "forged" } },
    );
    await api.authenticatedFetch(
      "/api/v1/platform/evaluation-adjacent/private",
    );
    await api.authenticatedFetch("/api/v1/institution/writing-reviews");
    const headers = fetch.mock.calls.map(
      ([, init]) => new Headers(init?.headers),
    );
    expect(headers[0].has("X-PrufAI-Organisation-ID")).toBe(false);
    expect(headers[0].get("Authorization")).toBe("Bearer technical-test-token");
    expect(headers[1].get("X-PrufAI-Organisation-ID")).toBe("selected-school");
    expect(headers[2].get("X-PrufAI-Organisation-ID")).toBe("selected-school");
  });
  it("uses bounded safe metadata and exact private references, retaining stale-conflict failure", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(
        async () => new Response(JSON.stringify(workbench), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetch);
    await referenceApi.summaries(20);
    await referenceApi.workbench(id);
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/platform/control-center/evaluation/references?offset=20&limit=20",
    );
    expect(fetch.mock.calls[1][0]).toBe(
      `/api/v1/platform/evaluation/references/${id}/workbench`,
    );
    expect(() => referenceApi.workbench("../private")).toThrow(
      "exact reference identity",
    );
    fetch.mockResolvedValueOnce(new Response("{}", { status: 409 }));
    await expect(
      referenceApi.act(id, "judgments", { expected_checksum: checksum }),
    ).rejects.toThrow("immutable history");
  });
});
