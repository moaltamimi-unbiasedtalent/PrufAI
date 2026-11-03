import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import {
  speakingFoundationApi,
  buildHumanAudioCriteria,
  eligibleSpeakingRubrics,
  type PublishedSpeakingRubric,
  type SpeakingSubmission,
} from "./app/institution/speaking-reviews/foundation-api";
import {
  HumanAudioCriterionFields,
  type HumanAudioCriterionEntry,
} from "./app/institution/speaking-reviews/human-criteria";
import { speakingReviewApi } from "./app/institution/speaking-reviews/review-api";
import type { RubricEvidence } from "./app/institution/writing-reviews/governance-ui";

const rubric: RubricEvidence = {
  id: "rubric-v1",
  checksum: "r".repeat(64),
  criteria: [{ code: "TASK", max_points: "3" }],
  performance_levels: [{ code: "CLEAR", label: "Clear" }],
  aggregation_policy: { method: "SUM" },
  evidence_policy: { mode: "AUDIO", required: true, time_unit: "milliseconds" },
};
const audio = {
  media_asset_id: "asset-a",
  sha256: "a".repeat(64),
  duration_ms: 1500,
  candidate_response_revision_id: "response-r1",
};
const value: HumanAudioCriterionEntry = {
  points: "2",
  level: "CLEAR",
  rationale: "Private bounded audio judgment.",
  spans: [{ startMs: "0", endMs: "1000" }],
};
const operationId = "operation-a";
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("HUMAN_ONLY Speaking exact evidence contracts", () => {
  beforeEach(() => {
    setSessionToken("reviewer-token");
    setOrganisationContext("org-a");
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });
  it("initializes from the exact submitted response revision and pinned rubric without accepting a browser asset authority", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(json({ id: "assessment-a" }));
    vi.stubGlobal("fetch", fetch);
    await speakingFoundationApi.initialize(
      "org-a",
      {
        attempt_id: "attempt-a",
        exam_version_item_binding_id: "binding-a",
        candidate_response_revision_id: "revision-a",
        response_checksum: "response-sum",
        candidate_media_asset_id: "must-not-authorize",
      } as SpeakingSubmission,
      { id: "rubric-a", checksum: "rubric-sum" } as PublishedSpeakingRubric,
      operationId,
    );
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/subjective-assessments/human-speaking",
    );
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      operation_id: operationId,
      attempt_id: "attempt-a",
      exam_version_item_binding_id: "binding-a",
      candidate_response_revision_id: "revision-a",
      response_checksum: "response-sum",
      rubric_version_id: "rubric-a",
      rubric_checksum: "rubric-sum",
    });
    const headers = new Headers(fetch.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-token");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
  });
  it("sends human decisions to the Speaking-owned endpoints and fetches an explicit historical version", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({ id: "v1" }));
    vi.stubGlobal("fetch", fetch);
    const criteria = buildHumanAudioCriteria(rubric, { TASK: value }, audio);
    await speakingFoundationApi.propose(
      "org-a",
      "v1",
      "sum",
      criteria,
      operationId,
    );
    await speakingFoundationApi.approve("org-a", "v1", "sum", operationId);
    await speakingFoundationApi.decide(
      "org-a",
      "v1",
      "sum",
      "CHANGES_REQUESTED",
      "Inspect the span.",
      operationId,
    );
    await speakingFoundationApi.successor(
      "org-a",
      "v1",
      "sum",
      "Governed reassessment.",
      operationId,
    );
    await speakingReviewApi.detail("org-a", "assessment-a", undefined, "v1");
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/organisations/org-a/speaking-reviews/v1/human-proposal",
      "/api/v1/organisations/org-a/speaking-reviews/v1/human-approve",
      "/api/v1/organisations/org-a/speaking-reviews/v1/human-decision",
      "/api/v1/organisations/org-a/speaking-reviews/v1/human-successor",
      "/api/v1/organisations/org-a/speaking-reviews/assessment-a?version_id=v1",
    ]);
    expect(
      JSON.parse(fetch.mock.calls[0][1]?.body as string).criteria[0],
    ).toEqual({
      criterion_code: "TASK",
      points: "2",
      level: "CLEAR",
      rationale: value.rationale,
      evidence: [
        {
          asset_id: audio.media_asset_id,
          audio_sha256: audio.sha256,
          candidate_response_revision_id: audio.candidate_response_revision_id,
          start_ms: 0,
          end_ms: 1000,
          time_unit: "milliseconds",
        },
      ],
    });
    expect(fetch.mock.calls[0][1]?.body).not.toMatch(
      /ai_run|confidence|quote|transcript/,
    );
  });
  it.each([403, 409, 503])(
    "preserves HTTP %s rather than claiming a completed decision",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            json({ detail: "Unavailable governed evidence" }, status),
          ),
      );
      await expect(
        speakingFoundationApi.approve("org-a", "v1", "sum", operationId),
      ).rejects.toMatchObject({ status });
    },
  );
  it("offers only the pinned published SPEAKING rubric from the exact pack/task", () => {
    const source = {
      rubric_version_id: "rubric-v1",
      standard_pack_version_id: "pack-v1",
      task_spec_id: "task-v1",
    } as SpeakingSubmission;
    const pinned = {
      ...rubric,
      skill: "SPEAKING",
      version_label: "v1",
      rubric_id: "rubric-parent",
      name: "Pinned Speaking rubric",
      ai_assessment_policy: { mode: "HUMAN_ONLY", enabled: false },
      status: "PUBLISHED",
      standard_pack_version_id: "pack-v1",
      task_spec_id: "task-v1",
    } as PublishedSpeakingRubric;
    expect(
      eligibleSpeakingRubrics(source, [
        pinned,
        { ...pinned, id: "other" },
        { ...pinned, skill: "WRITING" },
        { ...pinned, status: "DRAFT" },
        { ...pinned, task_spec_id: "wrong" },
        { ...pinned, ai_assessment_policy: { mode: "AI_ASSISTED" } },
        { ...pinned, evidence_policy: { mode: "TRANSCRIPT" } },
      ]),
    ).toEqual([pinned]);
    expect(
      eligibleSpeakingRubrics({ ...source, rubric_version_id: null }, [pinned]),
    ).toEqual([]);
  });
});

describe("bounded original-audio criterion inputs", () => {
  it.each([
    { startMs: "-1", endMs: "100" },
    { startMs: "100", endMs: "100" },
    { startMs: "101", endMs: "100" },
    { startMs: "0", endMs: "1501" },
    { startMs: "0.5", endMs: "100" },
    { startMs: "", endMs: "100" },
    { startMs: "0", endMs: "Infinity" },
  ])("rejects invalid millisecond span %j", (span) => {
    expect(() =>
      buildHumanAudioCriteria(
        rubric,
        { TASK: { ...value, spans: [span] } },
        audio,
      ),
    ).toThrow("whole milliseconds");
  });
  it("requires exact audio provenance and complete bounded criterion values", () => {
    expect(() =>
      buildHumanAudioCriteria(
        rubric,
        { TASK: value },
        { ...audio, duration_ms: 0 },
      ),
    ).toThrow("Verified duration");
    expect(() =>
      buildHumanAudioCriteria(
        rubric,
        { TASK: { ...value, points: "4" } },
        audio,
      ),
    ).toThrow("published rubric bounds");
    expect(() =>
      buildHumanAudioCriteria(
        rubric,
        { TASK: { ...value, level: "INVENTED" } },
        audio,
      ),
    ).toThrow("published rubric performance level");
    expect(() =>
      buildHumanAudioCriteria(
        rubric,
        { TASK: { ...value, rationale: " " } },
        audio,
      ),
    ).toThrow("Complete the human judgment");
    expect(() =>
      buildHumanAudioCriteria(rubric, { TASK: { ...value, spans: [] } }, audio),
    ).toThrow("original audio reference");
  });
  it("renders original-audio spans and private rationale without prefilled scores, transcript quotes or model confidence", () => {
    const html = renderToStaticMarkup(
      <HumanAudioCriterionFields
        criteria={rubric.criteria}
        levels={rubric.performance_levels}
        durationMs={1500}
        values={{}}
        disabled={false}
        onChange={() => undefined}
      />,
    );
    expect(html).toContain("Audio start ms for TASK span 1");
    expect(html).toContain("Audio end ms for TASK span 1");
    expect(html).toContain("Private human rationale for TASK");
    expect(html).not.toMatch(/transcript|quote|confidence|model/);
    expect(html).not.toContain('value="2"');
  });
});
