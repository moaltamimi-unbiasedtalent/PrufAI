import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import {
  completedSpeakingProposal,
  exactAISpeakingSource,
  speakingExecutionApi,
  type AISpeakingJob,
  type AISpeakingProfile,
  type AISpeakingSource,
} from "./app/institution/speaking-reviews/execution-api";
import {
  AISpeakingExecutionPanel,
  AISpeakingJobView,
} from "./app/institution/speaking-reviews/execution-panel";
import {
  AISpeakingExecutionEvidence,
  AISpeakingReviewControls,
  buildAISpeakingCorrections,
  canReviewExecutedAISpeaking,
  speakingAIReviewApi,
  verifiedAISpeakingProvenance,
} from "./app/institution/speaking-reviews/ai-review";
import type {
  AudioEvidenceAsset,
  SpeakingReview,
} from "./app/institution/speaking-reviews/review-api";
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
const source: AISpeakingSource = {
  attempt_id: "attempt-a",
  exam_version_id: "exam-a",
  exam_version_item_binding_id: "binding-a",
  question_bank_item_version_id: "item-a",
  candidate_response_revision_id: "revision-a",
  response_checksum: "response-sum",
  standard_pack_version_id: "pack-a",
  standard_pack_checksum: "pack-sum",
  rubric_version_id: "rubric-a",
  rubric_checksum: "rubric-sum",
  assessment_mode: "AI_ASSISTED",
  evidence_mode: "AUDIO",
  candidate_media_asset_id: "audio-a",
  audio_sha256: "audio-sum",
  duration_ms: 1500,
  recording_policy_checksum: "recording-sum",
  assessment_policy_checksum: "policy-sum",
  speech_transcription_version_id: null,
  transcript_checksum: null,
  task_label: "Technical audio practice",
};
const profile: AISpeakingProfile = {
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
  name: "TEST ONLY Speaking",
  provider: "DETERMINISTIC_TEST",
  model_name: "speaking-fixture-v1",
  adapter_version: "1",
  test_only: true,
  availability: "TEST_ONLY",
  unavailable_reason: null,
};
const job: AISpeakingJob = {
  ...source,
  ...profile,
  id: "job-a",
  organisation_id: "org-a",
  operation_id: "op-a",
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
const audio: AudioEvidenceAsset = {
  media_asset_id: "audio-a",
  sha256: "audio-sum",
  duration_ms: 1500,
  status: "READY",
  byte_size: 1000,
  content_type: "audio/webm",
  validation_provenance: { complete_input: true },
  saved_response_revision: 1,
  candidate_response_revision_id: "revision-a",
  attachment_status: "ATTACHED",
  attempt_id: "attempt-a",
  binding_id: "binding-a",
  item_version_id: "item-a",
  exam_version_id: "exam-a",
  recording_session_id: "recording-a",
};
const review: SpeakingReview = {
  assessment_id: "assessment-a",
  assessment_version_id: "version-a",
  version_id: "version-a",
  attempt_id: "attempt-a",
  status: "AI_PROPOSED",
  checksum: "assessment-sum",
  assessment_origin: "AI",
  assessment_mode: "AI_ASSISTED",
  evidence_mode: "AUDIO",
  original_audio_primary_evidence: true,
  derived_transcription: false,
  candidate_media_asset_id: "audio-a",
  audio_sha256: "audio-sum",
  speech_transcription_version_id: null,
  transcript_checksum: null,
  evidence_modes: { AUDIO: true },
  ai_proposal: {},
  human_approved_values: {},
  confidence: {},
  consistency: {},
  criteria: [],
  disagreement: {},
  review_required: true,
  transcript_available: false,
  candidate_response_revision_id: "revision-a",
  response_checksum: "response-sum",
  rubric_checksum: "rubric-sum",
  exam_version_id: "exam-a",
  exam_version_item_binding_id: "binding-a",
  question_bank_item_version_id: "item-a",
  standard_pack_version_id: "pack-a",
  standard_pack_checksum: "pack-sum",
  recording_policy_checksum: "recording-sum",
  assessment_policy_checksum: "policy-sum",
  ai_run_id: "run-a",
  human_review_policy: { required: true, separation_of_duties: true },
  rubric: {
    id: "rubric-a",
    checksum: "rubric-sum",
    rubric_id: "rubric-root",
    version_label: "v1",
    status: "PUBLISHED",
    standard_pack_version_id: "pack-a",
    task_spec_id: "task-a",
    skill: "SPEAKING",
    review: null,
    criteria: [{ code: "TASK", max_points: "3" }],
    performance_levels: [{ code: "CLEAR" }],
    aggregation_policy: { method: "SUM" },
    evidence_policy: {
      mode: "AUDIO",
      required: true,
      time_unit: "milliseconds",
      minimum_references: 1,
    },
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
    attempt_id: "attempt-a",
    exam_version_id: "exam-a",
    exam_version_item_binding_id: "binding-a",
    question_bank_item_version_id: "item-a",
    standard_pack_version_id: "pack-a",
    standard_pack_checksum: "pack-sum",
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
    candidate_response_revision_id: "revision-a",
    response_checksum: "response-sum",
    rubric_version_id: "rubric-a",
    rubric_checksum: "rubric-sum",
    candidate_media_asset_id: "audio-a",
    audio_sha256: "audio-sum",
    duration_ms: 1500,
    recording_policy_checksum: "recording-sum",
    assessment_policy_checksum: "policy-sum",
    evidence_mode: "AUDIO",
    speech_transcription_version_id: null,
    transcript_checksum: null,
    test_only: true,
    provider: "DETERMINISTIC_TEST",
    model_name: "speaking-fixture-v1",
    adapter_version: "1",
  },
};
const judgment = {
  points: "2",
  level: "CLEAR",
  rationale: "Independent listening judgment.",
  spans: [{ startMs: "0", endMs: "1000" }],
};

describe("governed AI Speaking execution UI contracts", () => {
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
  it("requests only exact evidence and approved configuration pins without audio bytes, private location, transcript or client proposal", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json(job));
    vi.stubGlobal("fetch", fetch);
    await speakingExecutionApi.request("org-a", source, profile, "op-a");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/speaking-executions",
    );
    const body = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(body).toEqual({
      operation_id: "op-a",
      attempt_id: "attempt-a",
      exam_version_item_binding_id: "binding-a",
      candidate_response_revision_id: "revision-a",
      response_checksum: "response-sum",
      candidate_media_asset_id: "audio-a",
      audio_sha256: "audio-sum",
      duration_ms: 1500,
      recording_policy_checksum: "recording-sum",
      assessment_policy_checksum: "policy-sum",
      rubric_version_id: "rubric-a",
      rubric_checksum: "rubric-sum",
      execution_profile_id: "workflow-a",
      execution_profile_version: "bundle-sum",
      model_profile_id: "model-a",
      prompt_version_id: "prompt-a",
      agent_version_id: "agent-a",
      workflow_version_id: "workflow-a",
    });
    expect(body).not.toHaveProperty("transcript");
    expect(body).not.toHaveProperty("audio_bytes");
    expect(body).not.toHaveProperty("storage_key");
    expect(body).not.toHaveProperty("ai_run_id");
    expect(body).not.toHaveProperty("proposal");
    const headers = new Headers(fetch.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-token");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
  });
  it("uses real job/read/retry/cancel endpoints", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json(job));
    vi.stubGlobal("fetch", fetch);
    await speakingExecutionApi.job("org-a", "job-a");
    await speakingExecutionApi.retry("org-a", "job-a");
    await speakingExecutionApi.cancel("org-a", "job-a");
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/organisations/org-a/speaking-executions/job-a",
      "/api/v1/organisations/org-a/speaking-executions/job-a/retry",
      "/api/v1/organisations/org-a/speaking-executions/job-a/cancel",
    ]);
  });
  it.each([403, 409, 503])(
    "preserves HTTP %s without claiming execution",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(json({ detail: "UNAVAILABLE" }, status)),
      );
      await expect(
        speakingExecutionApi.request("org-a", source, profile, "op-a"),
      ).rejects.toMatchObject({ status });
    },
  );
  it("rejects every mismatched source, media and policy pin and unavailable transcript modes", () => {
    expect(exactAISpeakingSource(source, job)).toBe(true);
    for (const key of [
      "attempt_id",
      "exam_version_id",
      "exam_version_item_binding_id",
      "question_bank_item_version_id",
      "candidate_response_revision_id",
      "response_checksum",
      "standard_pack_version_id",
      "standard_pack_checksum",
      "rubric_version_id",
      "rubric_checksum",
      "candidate_media_asset_id",
      "audio_sha256",
      "recording_policy_checksum",
      "assessment_policy_checksum",
    ])
      expect(exactAISpeakingSource(source, { ...job, [key]: "other" })).toBe(
        false,
      );
    expect(exactAISpeakingSource(source, { ...job, duration_ms: 1600 })).toBe(
      false,
    );
    for (const changes of [
      { assessment_mode: "HUMAN_ONLY" },
      { evidence_mode: "TRANSCRIPT" },
      { speech_transcription_version_id: "transcript-a" },
      { transcript_checksum: "sum" },
    ])
      expect(
        exactAISpeakingSource(source, { ...job, ...changes } as AISpeakingJob),
      ).toBe(false);
  });
  it("requires actual completed run and real pending handoff references before linking", () => {
    expect(completedSpeakingProposal(job)).toBe(true);
    for (const changes of [
      { status: "RUNNING" },
      { ai_run_status: "RUNNING" },
      { ai_run_id: null },
      { review_id: null },
      { review_status: "NOT_REAL" },
      { provider: "OPENAI", model_name: "text-only-model", test_only: false },
      { model_name: "wrong-fixture" },
      { test_only: false },
      { duration_ms: 0 },
      { recording_policy_checksum: "" },
      { transcript_checksum: "unrequested-transcript" },
    ])
      expect(
        completedSpeakingProposal({ ...job, ...changes } as AISpeakingJob),
      ).toBe(false);
    const markup = renderToStaticMarkup(
      <AISpeakingJobView job={{ ...job, status: "QUEUED" }} />,
    );
    expect(markup).not.toContain("Inspect AI Speaking proposal");
  });
  it("labels TEST ONLY technical proposals and never claims acoustic understanding or approved score", () => {
    const markup = renderToStaticMarkup(<AISpeakingJobView job={job} />);
    expect(markup).toContain("TEST ONLY");
    expect(markup).toContain("not authentic speech assessment");
    expect(markup).toContain("PENDING");
    expect(markup).toContain("human decision remains mandatory");
    expect(markup).toContain("/institution/speaking-reviews/assessment-a");
    expect(markup).toContain("None · AUDIO evidence only");
  });
  it("hides execution without institution authority", () => {
    actor.permissions = [];
    expect(renderToStaticMarkup(<AISpeakingExecutionPanel />)).toBe("");
  });
  it("requires exact run, original audio, rubric, response and policy agreement on review", () => {
    expect(verifiedAISpeakingProvenance(review, audio)).toBe(true);
    for (const changes of [
      { execution_status: "QUEUED" },
      { ai_run_status: "RUNNING" },
      { ai_run_id: "other" },
      { response_checksum: "other" },
      { rubric_checksum: "other" },
      { audio_sha256: "other" },
      { attempt_id: "other" },
      { exam_version_id: "other" },
      { exam_version_item_binding_id: "other" },
      { question_bank_item_version_id: "other" },
      { standard_pack_version_id: "other" },
      { standard_pack_checksum: "other" },
      { candidate_media_asset_id: "other" },
      { duration_ms: 1200 },
      { recording_policy_checksum: "other" },
      { assessment_policy_checksum: "other" },
      { speech_transcription_version_id: "other" },
      { transcript_checksum: "other" },
      { test_only: false },
    ])
      expect(
        verifiedAISpeakingProvenance(
          {
            ...review,
            execution_provenance: {
              ...review.execution_provenance!,
              ...changes,
            } as unknown as NonNullable<SpeakingReview["execution_provenance"]>,
          },
          audio,
        ),
      ).toBe(false);
    expect(
      verifiedAISpeakingProvenance(review, {
        ...audio,
        candidate_response_revision_id: "other",
      }),
    ).toBe(false);
    expect(
      verifiedAISpeakingProvenance(review, { ...audio, attempt_id: "other" }),
    ).toBe(false);
    expect(
      verifiedAISpeakingProvenance({ ...review, rubric: null }, audio),
    ).toBe(false);
  });
  it("requires current pending checksum and independently authorized human according to published policy", () => {
    expect(
      canReviewExecutedAISpeaking(
        review,
        audio,
        ["subjective.review"],
        "reviewer-a",
      ),
    ).toBe(true);
    expect(
      canReviewExecutedAISpeaking(
        review,
        audio,
        ["subjective.read"],
        "reviewer-a",
      ),
    ).toBe(false);
    expect(
      canReviewExecutedAISpeaking(
        review,
        audio,
        ["subjective.review"],
        "requester-a",
      ),
    ).toBe(false);
    for (const changes of [
      { human_review_policy: undefined },
      { is_current_version: false },
      { status: "APPROVED" },
      { review: { ...review.review!, status: "APPROVED" } },
      { review: { ...review.review!, reviewed_checksum: "stale" } },
    ])
      expect(
        canReviewExecutedAISpeaking(
          { ...review, ...changes },
          audio,
          ["subjective.review"],
          "reviewer-a",
        ),
      ).toBe(false);
    expect(
      canReviewExecutedAISpeaking(
        {
          ...review,
          human_review_policy: { required: true, separation_of_duties: false },
        },
        audio,
        ["subjective.review"],
        "requester-a",
      ),
    ).toBe(true);
  });
  it("withholds completion claims when provenance does not match original audio", () => {
    const markup = renderToStaticMarkup(
      <AISpeakingExecutionEvidence
        review={review}
        audio={{ ...audio, sha256: "wrong" }}
      />,
    );
    expect(markup).toContain("not verified");
    expect(markup).not.toContain("TEST ONLY AI PROPOSAL");
  });
  it("starts all independent human fields blank and validates bounded exact original audio corrections", () => {
    const markup = renderToStaticMarkup(
      <AISpeakingReviewControls
        review={review}
        audio={audio}
        organisationId="org-a"
        permissions={["subjective.review"]}
        userId="reviewer-a"
        onPersist={() => {}}
      />,
    );
    expect(markup).toContain('value=""');
    expect(markup).toContain("may differ from the AI values");
    expect(markup).toContain("independently judge each proposed audio span");
    expect(() =>
      buildAISpeakingCorrections(review.rubric!, {}, audio, "revision-a"),
    ).toThrow("Complete");
    expect(
      buildAISpeakingCorrections(
        review.rubric!,
        { TASK: judgment },
        audio,
        "revision-a",
      ),
    ).toEqual({
      TASK: {
        approved_points: "2",
        approved_level: "CLEAR",
        rationale: judgment.rationale,
        evidence: [
          {
            type: "AUDIO",
            candidate_media_asset_id: "audio-a",
            audio_sha256: "audio-sum",
            start_ms: 0,
            end_ms: 1000,
          },
        ],
      },
    });
    for (const value of [
      { ...judgment, points: "4" },
      { ...judgment, level: "INVENTED" },
      { ...judgment, spans: [{ startMs: "0", endMs: "1501" }] },
      { ...judgment, rationale: "" },
    ])
      expect(() =>
        buildAISpeakingCorrections(
          review.rubric!,
          { TASK: value },
          audio,
          "revision-a",
        ),
      ).toThrow();
    expect(() =>
      buildAISpeakingCorrections(
        review.rubric!,
        { TASK: judgment, EXTRA: judgment },
        audio,
        "revision-a",
      ),
    ).toThrow("only");
  });
  it("persists explicit human corrections and version checksum through existing review governance", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({ status: "APPROVED" }));
    vi.stubGlobal("fetch", fetch);
    const payload = {
      operation_id: "human-op",
      expected_checksum: "assessment-sum",
      reason: "Independent original audio review",
      corrections: buildAISpeakingCorrections(
        review.rubric!,
        { TASK: judgment },
        audio,
        "revision-a",
      ),
    };
    await speakingAIReviewApi.decide("org-a", "version-a", "APPROVED", payload);
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/subjective-assessments/version-a/approve",
    );
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(payload);
    await speakingAIReviewApi.decide(
      "org-a",
      "version-a",
      "CHANGES_REQUESTED",
      {
        operation_id: "decision-op",
        expected_checksum: "assessment-sum",
        reason: "Check exact acoustic evidence.",
      },
    );
    expect(fetch.mock.calls[1][0]).toBe(
      "/api/v1/organisations/org-a/subjective-assessments/version-a/decision",
    );
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body)).decision).toBe(
      "CHANGES_REQUESTED",
    );
  });
  it("limits platform operations to safe allowlisted metadata", () => {
    const view = metadataSections["ai-operations"].views["speaking-proposals"];
    const markup = renderToStaticMarkup(
      <MetadataTable
        caption={view.label}
        columns={view.columns}
        rows={[
          {
            id: "job-a",
            status: "COMPLETED",
            human_review_status: "PENDING",
            assessment_status: "AI_PROPOSED",
            audio_bytes: "PRIVATE_AUDIO",
            storage_key: "PRIVATE_LOCATION",
            transcript: "PRIVATE_SPEECH",
            prompt: "PRIVATE_PROMPT",
            api_key: "PRIVATE_KEY",
          },
        ]}
      />,
    );
    expect(markup).toContain("PENDING");
    expect(markup).toContain("AI_PROPOSED");
    for (const forbidden of [
      "PRIVATE_AUDIO",
      "PRIVATE_LOCATION",
      "PRIVATE_SPEECH",
      "PRIVATE_PROMPT",
      "PRIVATE_KEY",
    ])
      expect(markup).not.toContain(forbidden);
  });
});
