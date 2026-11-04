import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import {
  completedWritingProposal,
  exactAIWritingSource,
  writingExecutionApi,
  type AIWritingJob,
  type AIWritingProfile,
  type AIWritingSource,
} from "./app/institution/writing-reviews/execution-api";
import {
  AIWritingExecutionPanel,
  AIWritingJobView,
} from "./app/institution/writing-reviews/execution-panel";
import {
  AIWritingExecutionEvidence,
  AIWritingReviewerFields,
  buildAIWritingCorrections,
  canReviewExecutedAIWriting,
  verifiedAIWritingProvenance,
} from "./app/institution/writing-reviews/ai-review";
import {
  writingReviewApi,
  type WritingReview,
} from "./app/institution/writing-reviews/review-api";
import { metadataSections, MetadataTable } from "./app/control-center/metadata";

let actor = {
  user: { id: "reviewer-a" },
  organisationId: "org-a",
  permissions: ["subjective.read", "subjective.assess", "subjective.review"],
};
vi.mock("./lib/auth", () => ({ useAuth: () => actor }));
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const source: AIWritingSource = {
  attempt_id: "attempt-a",
  exam_version_id: "exam-a",
  exam_version_item_binding_id: "binding-a",
  question_bank_item_version_id: "item-a",
  candidate_response_revision_id: "response-a",
  response_checksum: "response-sum",
  standard_pack_version_id: "pack-a",
  standard_pack_checksum: "pack-sum",
  rubric_version_id: "rubric-a",
  rubric_checksum: "rubric-sum",
  assessment_mode: "AI_ASSISTED",
  task_label: "Original technical Writing task",
};
const profile: AIWritingProfile = {
  execution_profile_id: "workflow-a",
  execution_profile_version: "bundle-sum",
  model_profile_id: "model-a",
  model_version: "model-sum",
  prompt_version_id: "prompt-a",
  prompt_checksum: "prompt-sum",
  agent_version_id: "agent-a",
  agent_checksum: "agent-sum",
  workflow_version_id: "workflow-a",
  workflow_checksum: "workflow-sum",
  name: "TEST ONLY Writing execution",
  provider: "DETERMINISTIC_TEST",
  model_name: "writing-fixture-v1",
  adapter_version: "1",
  test_only: true,
  availability: "TEST_ONLY",
  unavailable_reason: null,
};
const job: AIWritingJob = {
  ...source,
  ...profile,
  id: "job-a",
  organisation_id: "org-a",
  operation_id: "operation-a",
  status: "COMPLETED",
  ai_run_id: "run-a",
  ai_run_status: "SUCCEEDED",
  assessment_id: "assessment-a",
  assessment_version_id: "version-a",
  review_id: "review-a",
  review_status: "PENDING",
  attempt_count: 1,
  max_attempts: 3,
  created_at: "2026-11-04T12:00:00Z",
  started_at: "2026-11-04T12:00:01Z",
  completed_at: "2026-11-04T12:00:02Z",
  failure_category: null,
  can_retry: false,
  can_cancel: false,
  state_history: [
    { status: "QUEUED", at: "2026-11-04T12:00:00Z", attempt_count: 0 },
    { status: "RUNNING", at: "2026-11-04T12:00:01Z", attempt_count: 1 },
    { status: "COMPLETED", at: "2026-11-04T12:00:02Z", attempt_count: 1 },
  ],
};
const review: WritingReview = {
  id: "assessment-a",
  version_id: "version-a",
  status: "AI_PROPOSED",
  checksum: "assessment-sum",
  assessment_origin: "AI",
  assessment_mode: "AI_ASSISTED",
  human_review_policy: { required: true, separation_of_duties: true },
  candidate_text:
    "Original private candidate text <script>untrusted()</script>",
  candidate_response_revision_id: source.candidate_response_revision_id,
  response_checksum: source.response_checksum,
  rubric_checksum: source.rubric_checksum,
  ai_run_id: "run-a",
  criteria: [],
  rubric: {
    id: source.rubric_version_id,
    checksum: source.rubric_checksum,
    criteria: [{ code: "TASK", max_points: "4" }],
    performance_levels: [{ code: "BASIC" }, { code: "STRONG" }],
    aggregation_policy: { method: "SUM" },
    evidence_policy: { required: true },
  },
  review: {
    id: "review-a",
    status: "PENDING",
    requested_by: "requester-a",
    requested_at: null,
    decided_by: null,
    decided_at: null,
    reviewed_checksum: "assessment-sum",
  },
  execution_provenance: {
    execution_job_id: "job-a",
    execution_status: "COMPLETED",
    ai_run_id: "run-a",
    ai_run_status: "SUCCEEDED",
    execution_profile_id: profile.execution_profile_id,
    profile_version: profile.execution_profile_version,
    model_profile_id: profile.model_profile_id,
    model_version: profile.model_version,
    prompt_version_id: profile.prompt_version_id,
    prompt_checksum: profile.prompt_checksum,
    agent_version_id: profile.agent_version_id,
    agent_checksum: profile.agent_checksum,
    workflow_version_id: profile.workflow_version_id,
    workflow_checksum: profile.workflow_checksum,
    candidate_response_revision_id: source.candidate_response_revision_id,
    response_checksum: source.response_checksum,
    rubric_version_id: source.rubric_version_id,
    rubric_checksum: source.rubric_checksum,
    test_only: true,
    provider: profile.provider,
    model_name: profile.model_name,
    adapter_version: "1",
  },
};

describe("durable AI Writing execution security contracts", () => {
  beforeEach(() => {
    setSessionToken("reviewer-token");
    setOrganisationContext("org-a");
    actor = {
      user: { id: "reviewer-a" },
      organisationId: "org-a",
      permissions: [
        "subjective.read",
        "subjective.assess",
        "subjective.review",
      ],
    };
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });

  it("posts exact source and approved registry pins without candidate text or a client-supplied AI run", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(json(job));
    vi.stubGlobal("fetch", fetch);
    await writingExecutionApi.request("org-a", source, profile, "operation-a");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/writing-executions",
    );
    const init = fetch.mock.calls[0][1]!;
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({
      operation_id: "operation-a",
      attempt_id: source.attempt_id,
      exam_version_item_binding_id: source.exam_version_item_binding_id,
      candidate_response_revision_id: source.candidate_response_revision_id,
      response_checksum: source.response_checksum,
      rubric_version_id: source.rubric_version_id,
      rubric_checksum: source.rubric_checksum,
      execution_profile_id: profile.execution_profile_id,
      execution_profile_version: profile.execution_profile_version,
      model_profile_id: profile.model_profile_id,
      prompt_version_id: profile.prompt_version_id,
      agent_version_id: profile.agent_version_id,
      workflow_version_id: profile.workflow_version_id,
    });
    expect(body).not.toHaveProperty("candidate_text");
    expect(body).not.toHaveProperty("ai_run_id");
    expect(body).not.toHaveProperty("proposal");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-token");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
  });

  it("uses actual persisted status, retry and cancellation endpoints", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json(job));
    vi.stubGlobal("fetch", fetch);
    await writingExecutionApi.job("org-a", "job-a");
    await writingExecutionApi.retry("org-a", "job-a");
    await writingExecutionApi.cancel("org-a", "job-a");
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/organisations/org-a/writing-executions/job-a",
      "/api/v1/organisations/org-a/writing-executions/job-a/retry",
      "/api/v1/organisations/org-a/writing-executions/job-a/cancel",
    ]);
  });

  it.each([403, 409, 503])(
    "preserves HTTP %s without a fake successful proposal",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(json({ detail: "EXECUTION_UNAVAILABLE" }, status)),
      );
      await expect(
        writingExecutionApi.request("org-a", source, profile, "operation-a"),
      ).rejects.toMatchObject({ status });
    },
  );

  it("rejects source/rubric/item/pack mismatches and HUMAN_ONLY input", () => {
    expect(exactAIWritingSource(source, job)).toBe(true);
    for (const key of [
      "candidate_response_revision_id",
      "response_checksum",
      "rubric_version_id",
      "rubric_checksum",
      "exam_version_item_binding_id",
      "question_bank_item_version_id",
      "standard_pack_version_id",
      "standard_pack_checksum",
    ])
      expect(exactAIWritingSource(source, { ...job, [key]: "other" })).toBe(
        false,
      );
    expect(
      exactAIWritingSource(
        {
          ...source,
          assessment_mode: "HUMAN_ONLY",
        } as unknown as AIWritingSource,
        job,
      ),
    ).toBe(false);
  });

  it("requires an actually completed run and complete handoff/review references", () => {
    expect(completedWritingProposal(job)).toBe(true);
    expect(completedWritingProposal({ ...job, ai_run_status: "RUNNING" })).toBe(
      false,
    );
    expect(completedWritingProposal({ ...job, review_id: null })).toBe(false);
    expect(completedWritingProposal({ ...job, test_only: false })).toBe(false);
    expect(completedWritingProposal({ ...job, rubric_checksum: "" })).toBe(
      false,
    );
    const markup = renderToStaticMarkup(
      <AIWritingJobView job={{ ...job, status: "RUNNING" }} />,
    );
    expect(markup).toContain("RUNNING");
    expect(markup).not.toContain("Inspect AI Writing proposal");
  });

  it("clearly labels deterministic technical output and mandatory pending human review", () => {
    const markup = renderToStaticMarkup(<AIWritingJobView job={job} />);
    expect(markup).toContain("TEST ONLY");
    expect(markup).toContain("not authentic AI assessment");
    expect(markup).toContain("PENDING");
    expect(markup).toContain("/institution/writing-reviews/assessment-a");
    expect(markup).toContain("human decision remains mandatory");
  });

  it("does not expose an execution panel without institution subjective-read authority", () => {
    actor.permissions = [];
    expect(renderToStaticMarkup(<AIWritingExecutionPanel />)).toBe("");
  });

  it("requires exact completed execution provenance and a matching pending independent review", () => {
    expect(verifiedAIWritingProvenance(review)).toBe(true);
    expect(
      canReviewExecutedAIWriting(review, ["subjective.review"], "reviewer-a"),
    ).toBe(true);
    expect(
      canReviewExecutedAIWriting(review, ["subjective.read"], "reviewer-a"),
    ).toBe(false);
    expect(
      canReviewExecutedAIWriting(review, ["subjective.review"], "requester-a"),
    ).toBe(false);
    expect(
      canReviewExecutedAIWriting(
        {
          ...review,
          human_review_policy: { required: true, separation_of_duties: false },
        },
        ["subjective.review"],
        "requester-a",
      ),
    ).toBe(true);
    expect(
      canReviewExecutedAIWriting(
        { ...review, human_review_policy: undefined },
        ["subjective.review"],
        "reviewer-a",
      ),
    ).toBe(false);
    expect(
      canReviewExecutedAIWriting(
        {
          ...review,
          review: { ...review.review!, reviewed_checksum: "stale" },
        },
        ["subjective.review"],
        "reviewer-a",
      ),
    ).toBe(false);
    for (const changes of [
      { ai_run_status: "RUNNING" },
      { ai_run_id: "other-run" },
      { response_checksum: "other-response" },
      { rubric_checksum: "other-rubric" },
      { test_only: false },
    ])
      expect(
        verifiedAIWritingProvenance({
          ...review,
          execution_provenance: { ...review.execution_provenance!, ...changes },
        }),
      ).toBe(false);
    expect(verifiedAIWritingProvenance({ ...review, rubric: null })).toBe(
      false,
    );
  });

  it("withholds completion claims for unmatched provenance", () => {
    const markup = renderToStaticMarkup(
      <AIWritingExecutionEvidence
        review={{
          ...review,
          execution_provenance: {
            ...review.execution_provenance!,
            response_checksum: "wrong",
          },
        }}
      />,
    );
    expect(markup).toContain("not verified");
    expect(markup).not.toContain("TEST ONLY AI PROPOSAL");
  });

  it("starts reviewer fields blank and permits an independent correction within rubric bounds", () => {
    const markup = renderToStaticMarkup(
      <AIWritingReviewerFields
        rubric={review.rubric!}
        values={{}}
        disabled={false}
        onChange={() => {}}
      />,
    );
    expect(markup).toContain('value=""');
    expect(markup).toContain("may differ from the AI values");
    expect(() => buildAIWritingCorrections(review.rubric!, {})).toThrow(
      "not copied automatically",
    );
    expect(
      buildAIWritingCorrections(review.rubric!, {
        TASK: { points: "3", level: "STRONG" },
      }),
    ).toEqual({ TASK: { approved_points: "3", approved_level: "STRONG" } });
    expect(() =>
      buildAIWritingCorrections(review.rubric!, {
        TASK: { points: "5", level: "STRONG" },
      }),
    ).toThrow("bounds");
    expect(() =>
      buildAIWritingCorrections(review.rubric!, {
        TASK: { points: "2", level: "INVENTED" },
      }),
    ).toThrow("published rubric");
    expect(() =>
      buildAIWritingCorrections(review.rubric!, {
        EXTRA: { points: "2", level: "BASIC" },
      }),
    ).toThrow("only");
  });

  it("submits exact checksum, operation, reason and explicit reviewer values through existing governance", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({ status: "APPROVED" }));
    vi.stubGlobal("fetch", fetch);
    const governance = {
      operation_id: "review-operation",
      expected_checksum: review.checksum,
      reason: "Independent rubric review",
      corrections: { TASK: { approved_points: "3", approved_level: "STRONG" } },
    };
    await writingReviewApi.approve("org-a", review.version_id, governance);
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(
      governance,
    );
    await writingReviewApi.decide(
      "org-a",
      review.version_id,
      "CHANGES_REQUESTED",
      {
        operation_id: "decision-operation",
        expected_checksum: review.checksum,
        reason: "Reconsider exact evidence.",
      },
    );
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({
      operation_id: "decision-operation",
      expected_checksum: review.checksum,
      reason: "Reconsider exact evidence.",
      decision: "CHANGES_REQUESTED",
    });
  });

  it("keeps platform Writing operations metadata-only", () => {
    const view = metadataSections["ai-operations"].views["writing-proposals"];
    const markup = renderToStaticMarkup(
      <MetadataTable
        caption={view.label}
        columns={view.columns}
        rows={[
          {
            id: "job-a",
            status: "COMPLETED",
            human_review_status: "PENDING",
            candidate_text: "PRIVATE_WRITING",
            prompt: "PRIVATE_PROMPT",
            api_key: "PRIVATE_CREDENTIAL",
          },
        ]}
      />,
    );
    expect(markup).toContain("job-a");
    expect(markup).toContain("PENDING");
    expect(markup).not.toContain("PRIVATE_WRITING");
    expect(markup).not.toContain("PRIVATE_PROMPT");
    expect(markup).not.toContain("PRIVATE_CREDENTIAL");
  });
});
