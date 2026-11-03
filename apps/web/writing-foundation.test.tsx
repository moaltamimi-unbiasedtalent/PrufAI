import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import { canViewRoute, type Membership } from "./lib/access";
import {
  buildHumanCriteria,
  eligibleWritingRubrics,
  humanWritingActions,
  rubricActions,
  writingFoundationApi,
  type PublishedWritingRubric,
  type RubricVersionDetail,
  type WritingSubmission,
} from "./app/institution/writing-reviews/foundation-api";
import {
  HumanCriterionFields,
  RubricEvidenceView,
  type HumanCriterionEntry,
  type RubricEvidence,
} from "./app/institution/writing-reviews/governance-ui";
import { writingReviewApi } from "./app/institution/writing-reviews/review-api";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const operation = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const rubric: RubricEvidence = {
  id: "rubric-v1",
  checksum: "r".repeat(64),
  criteria: [
    {
      code: "TASK",
      max_points: "4",
      descriptors: { BASIC: "Addresses the original task." },
    },
  ],
  performance_levels: [{ code: "BASIC", label: "Basic evidence" }],
  aggregation_policy: { method: "SUM" },
  evidence_policy: { required: false },
};
const entry: HumanCriterionEntry = {
  points: "2",
  level: "BASIC",
  reason: "The submitted response addresses the task.",
  evidence: "",
};

describe("authenticated rubric and HUMAN Writing contracts", () => {
  beforeEach(() => {
    setSessionToken("reviewer-token");
    setOrganisationContext("org-a");
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });
  it("pins real rubric parent/version and exact checksum for each governed publication action", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () =>
        json({
          id: rubric.id,
          status: "HUMAN_REVIEW",
          checksum: rubric.checksum,
        }),
      );
    vi.stubGlobal("fetch", fetch);
    for (const action of ["request-review", "approve", "publish"] as const)
      await writingFoundationApi.rubricAction(
        "org-a",
        "rubric-parent",
        rubric.id,
        action,
        rubric.checksum,
        operation,
      );
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(
      ["request-review", "approve", "publish"].map(
        (action) =>
          `/api/v1/organisations/org-a/rubrics/rubric-parent/versions/rubric-v1/${action}`,
      ),
    );
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      operation_id: operation,
      expected_checksum: rubric.checksum,
    });
    const headers = new Headers(fetch.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-token");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
  });
  it("initializes HUMAN assessment from selected submitted response and published rubric checksums without AI fields", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      json({
        id: "assessment-a",
        version_id: "human-v1",
        assessment_origin: "HUMAN",
        status: "REVIEW_REQUIRED",
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const result = await writingFoundationApi.initialize(
      "org-a",
      {
        attempt_id: "attempt-a",
        exam_version_item_binding_id: "binding-a",
        response_checksum: "response-sum",
      } as WritingSubmission,
      { id: rubric.id, checksum: rubric.checksum } as PublishedWritingRubric,
      operation,
    );
    expect(result.assessment_origin).toBe("HUMAN");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/subjective-assessments/human",
    );
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      operation_id: operation,
      attempt_id: "attempt-a",
      exam_version_item_binding_id: "binding-a",
      rubric_version_id: rubric.id,
      response_checksum: "response-sum",
      rubric_checksum: rubric.checksum,
    });
    expect(fetch.mock.calls[0][1]?.body).not.toContain("ai_run_id");
  });
  it("submits human values and decisions with stable identity/checksum while preserving the legacy AI endpoint", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({ id: "human-v1" }));
    vi.stubGlobal("fetch", fetch);
    const criteria = buildHumanCriteria(
      rubric,
      { TASK: entry },
      "Original candidate response",
    );
    await writingFoundationApi.propose(
      "org-a",
      "human-v1",
      "version-sum",
      criteria,
      operation,
    );
    await writingFoundationApi.approve(
      "org-a",
      "human-v1",
      "version-sum",
      operation,
    );
    await writingFoundationApi.decide(
      "org-a",
      "human-v1",
      "version-sum",
      "REJECTED",
      "Evidence does not satisfy the rubric.",
      operation,
    );
    await writingReviewApi.approve("org-a", "ai-v1");
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/organisations/org-a/subjective-assessments/human-v1/human-proposal",
      "/api/v1/organisations/org-a/subjective-assessments/human-v1/human-approve",
      "/api/v1/organisations/org-a/subjective-assessments/human-v1/human-decision",
      "/api/v1/organisations/org-a/subjective-assessments/ai-v1/approve",
    ]);
    const body = JSON.parse(fetch.mock.calls[0][1]?.body as string);
    expect(body).toMatchObject({
      operation_id: operation,
      expected_checksum: "version-sum",
      criteria: [
        { criterion_code: "TASK", points: "2", level: "BASIC", evidence: [] },
      ],
    });
    expect(body.criteria[0]).not.toHaveProperty("confidence");
    expect(body).not.toHaveProperty("ai_run_id");
  });
  it.each([403, 409, 503])(
    "preserves HTTP %s denial/conflict/outage for refresh and retry",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            json({ detail: "Governed action unavailable" }, status),
          ),
      );
      await expect(
        writingFoundationApi.approve("org-a", "human-v1", "sum", operation),
      ).rejects.toMatchObject({ status });
    },
  );
});

describe("real authority, state and separation of duties", () => {
  const full = {
    ...rubric,
    rubric_id: "rubric-parent",
    status: "HUMAN_REVIEW",
    review: {
      id: "review-a",
      status: "PENDING",
      requested_by: "author",
      reviewed_checksum: rubric.checksum,
    },
  } as RubricVersionDetail;
  it("requires exact pending rubric review and an independent authorised actor", () => {
    expect(rubricActions(full, ["rubric.manage"], "reviewer").approve).toBe(
      true,
    );
    expect(rubricActions(full, ["rubric.manage"], "author").approve).toBe(
      false,
    );
    expect(rubricActions(full, ["rubric.read"], "reviewer").approve).toBe(
      false,
    );
    expect(
      rubricActions(
        { ...full, checksum: "changed" },
        ["rubric.manage"],
        "reviewer",
      ).approve,
    ).toBe(false);
    expect(
      rubricActions(
        { ...full, status: "APPROVED" },
        ["rubric.manage"],
        "reviewer",
      ).publish,
    ).toBe(true);
    expect(
      rubricActions(
        { ...full, status: "PUBLISHED" },
        ["rubric.manage"],
        "reviewer",
      ),
    ).toEqual({ requestReview: false, approve: false, publish: false });
  });
  it("keeps HUMAN decisions distinct from AI and disallows self-approval or terminal mutations", () => {
    const source = {
      assessment_origin: "HUMAN",
      status: "IN_REVIEW",
      assessor_id: "assessor",
      checksum: "version-sum",
      review: { status: "PENDING", reviewed_checksum: "version-sum" },
    };
    expect(
      humanWritingActions(source, ["subjective.review"], "reviewer").decide,
    ).toBe(true);
    expect(
      humanWritingActions(source, ["subjective.review"], "assessor").decide,
    ).toBe(false);
    expect(
      humanWritingActions(
        { ...source, checksum: "changed" },
        ["subjective.review"],
        "reviewer",
      ).decide,
    ).toBe(false);
    expect(
      humanWritingActions(source, ["subjective.read"], "reviewer").decide,
    ).toBe(false);
    expect(
      humanWritingActions(
        { ...source, status: "APPROVED" },
        ["subjective.assess", "subjective.review"],
        "reviewer",
      ),
    ).toEqual({ propose: false, decide: false, decideAI: false });
    expect(
      humanWritingActions(
        { assessment_origin: "AI", status: "AI_PROPOSED" },
        ["subjective.review"],
        "reviewer",
      ),
    ).toEqual({ propose: false, decide: false, decideAI: true });
    expect(
      humanWritingActions(
        { assessment_origin: "UNSPECIFIED", status: "AI_PROPOSED" },
        ["subjective.review"],
        "reviewer",
      ).decideAI,
    ).toBe(false);
    expect(
      humanWritingActions(
        {
          assessment_origin: "UNSPECIFIED",
          status: "AI_PROPOSED",
          legacy_ai_review_eligible: true,
        },
        ["subjective.review"],
        "reviewer",
      ).decideAI,
    ).toBe(true);
  });
  it("requires real rubric read permission for publication navigation", () => {
    const member: Membership = {
      organisation_id: "org-a",
      organisation_name: "School",
      status: "ACTIVE",
      role_keys: ["EXAMINER"],
      permissions: ["organisation.read"],
    };
    expect(canViewRoute("/institution/rubrics", member)).toBe(false);
    expect(
      canViewRoute("/institution/rubrics/r/versions/v", {
        ...member,
        permissions: ["rubric.read"],
      }),
    ).toBe(true);
    expect(
      canViewRoute("/institution/rubrics", {
        ...member,
        status: "SUSPENDED",
        permissions: ["rubric.read"],
      }),
    ).toBe(false);
  });
});

describe("bounded human values and exact response evidence", () => {
  it("offers only the item-pinned published rubric, excluding alternate or missing references", () => {
    const source = {
      rubric_version_id: "rubric-v1",
      standard_pack_version_id: "pack-v1",
      task_spec_id: "task-v1",
    } as WritingSubmission;
    const pinned = {
      ...rubric,
      rubric_id: "parent",
      name: "Pinned",
      version_label: "v1",
      skill: "WRITING",
      status: "PUBLISHED",
      standard_pack_version_id: "pack-v1",
      task_spec_id: "task-v1",
    } as PublishedWritingRubric;
    expect(
      eligibleWritingRubrics(source, [pinned, { ...pinned, id: "rubric-v2" }]),
    ).toEqual([pinned]);
    expect(
      eligibleWritingRubrics({ ...source, rubric_version_id: null }, [pinned]),
    ).toEqual([]);
    expect(
      eligibleWritingRubrics(source, [{ ...pinned, status: "DRAFT" }]),
    ).toEqual([]);
  });
  it("creates no invented optional citation and validates the actual rubric level/point range", () => {
    const criteria = buildHumanCriteria(
      rubric,
      { TASK: entry },
      "Original response",
    );
    expect(criteria[0].evidence).toEqual([]);
    expect(() =>
      buildHumanCriteria(
        rubric,
        { TASK: { ...entry, points: "5" } },
        "Original response",
      ),
    ).toThrow("published rubric bounds");
    expect(() =>
      buildHumanCriteria(
        rubric,
        { TASK: { ...entry, level: "INVENTED_LEVEL" } },
        "Original response",
      ),
    ).toThrow("published rubric performance level");
    expect(() =>
      buildHumanCriteria(
        { ...rubric, evidence_policy: { required: true } },
        { TASK: entry },
        "Original response",
      ),
    ).toThrow("Exact response evidence is required");
  });
  it("uses Unicode codepoints for exact quote offsets including emoji, umlauts and combining characters without normalization", () => {
    const text = "🙂 Grüße aus Ko\u0308ln. 📝 Ich komme später.";
    const quote = "Ko\u0308ln. 📝";
    const [reference] = buildHumanCriteria(
      rubric,
      { TASK: { ...entry, evidence: quote } },
      text,
    )[0].evidence;
    expect(
      Array.from(text).slice(reference.start, reference.end).join(""),
    ).toBe(quote);
    expect(reference.start).toBeLessThan(text.indexOf(quote));
    expect(reference.end - reference.start).toBe(Array.from(quote).length);
    expect(() =>
      buildHumanCriteria(
        rubric,
        { TASK: { ...entry, evidence: "Köln" } },
        text,
      ),
    ).toThrow("exactly match");
  });
  it("renders actual rubric descriptors and human entry controls without default scores or AI labels", () => {
    const evidence = renderToStaticMarkup(
      <RubricEvidenceView rubric={rubric} />,
    );
    expect(evidence).toContain("Addresses the original task.");
    expect(evidence).toContain("Maximum points: 4");
    const fields = renderToStaticMarkup(
      <HumanCriterionFields
        criteria={rubric.criteria}
        levels={rubric.performance_levels}
        values={{}}
        disabled={false}
        evidenceRequired={false}
        onChange={() => undefined}
      />,
    );
    expect(fields).toContain('min="0"');
    expect(fields).toContain('max="4"');
    expect(fields).toContain("Human rationale for TASK");
    expect(fields).toContain("(optional)");
    expect(fields).not.toContain('value="2"');
    expect(fields).not.toContain("AI PROPOSAL");
  });
});
