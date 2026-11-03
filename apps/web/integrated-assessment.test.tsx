import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  assessmentApi,
  resultActions,
  type ResultVersion,
} from "./app/institution/assessment-api";
import {
  appealActions,
  appealApi,
  type AppealDetail,
} from "./app/institution/appeals/appeal-api";
import {
  exactReleasedScore,
  type ReleaseDetail,
} from "./app/institution/releases/release-api";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import { canViewRoute, type Membership } from "./lib/access";
import AssignAssessmentPage from "./app/institution/assignments/page";
import ResultReviewPage from "./app/institution/results/[resultId]/page";

let permissions: string[] = [];
vi.mock("./lib/auth", () => ({
  useAuth: () => ({ organisationId: "org-a", permissions }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const version = {
  result_id: "result-a",
  result_version_id: "version-a",
  version_number: 1,
  attempt_id: "attempt-a",
  scoring_run_id: "run-a",
  status: "REVIEW_REQUIRED",
  checksum: "checksum-a",
  review: null,
} as ResultVersion;
const appeal = {
  id: "appeal-a",
  status: "UNDER_REVIEW",
  category: "SCORING",
  result_id: "result-a",
  challenged_result_version_id: "version-a",
  challenged_result_checksum: "r".repeat(64),
  current_version_number: 2,
  current_checksum: "a".repeat(64),
  evidence_bundle_checksum: "e".repeat(64),
} as AppealDetail;

describe("institution integration API contract", () => {
  beforeEach(() => {
    setSessionToken("institution-bearer");
    setOrganisationContext("org-a");
    permissions = [];
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });

  it("loads only server-provided candidates and deliverable versions through the selected institution", async () => {
    const options = {
      candidates: [{ id: "candidate-a", display_name: "Active Candidate" }],
      exam_versions: [
        {
          id: "exam-version-a",
          status: "PUBLISHED",
          classification: "PRACTICE",
        },
      ],
    };
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(json(options));
    vi.stubGlobal("fetch", fetch);
    expect(await assessmentApi.options("org-a")).toEqual(options);
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/exam-assignment-options",
    );
    const headers = new Headers(fetch.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer institution-bearer");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
  });
  it("pins assignment to selected candidate and exact exam version without inserting scores or attempts", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(json({ id: "assignment-a", status: "ASSIGNED" }, 201));
    vi.stubGlobal("fetch", fetch);
    await assessmentApi.assign("org-a", "candidate-a", "exam-version-a");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/exam-assignments",
    );
    expect(fetch.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      candidate_user_id: "candidate-a",
      exam_version_id: "exam-version-a",
      maximum_attempts: 1,
    });
  });
  it("uses actual scoring and separate governed review/approval endpoints with persisted run inspection", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({ id: "run-a" }));
    vi.stubGlobal("fetch", fetch);
    await assessmentApi.score("org-a", "attempt-a");
    await assessmentApi.versions("org-a", "result-a");
    await assessmentApi.scoringRun("org-a", "run-a");
    await assessmentApi.requestReview("org-a", "version-a");
    await assessmentApi.approve("org-a", "version-a");
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/organisations/org-a/exam-attempts/attempt-a/score",
      "/api/v1/organisations/org-a/results/result-a/versions",
      "/api/v1/organisations/org-a/scoring-runs/run-a",
      "/api/v1/organisations/org-a/result-versions/version-a/request-review",
      "/api/v1/organisations/org-a/result-versions/version-a/approve",
    ]);
    expect(fetch.mock.calls[0][1]?.method).toBe("POST");
    expect(fetch.mock.calls[3][1]?.method).toBe("POST");
    expect(fetch.mock.calls[4][1]?.method).toBe("POST");
  });
  it.each([403, 409])(
    "preserves HTTP %s as denial/conflict for explicit refresh handling",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(json({ detail: "stale or forbidden" }, status)),
      );
      await expect(
        assessmentApi.approve("org-a", "version-a"),
      ).rejects.toMatchObject({ status });
      await expect(assessmentApi.approve("org-a", "version-a")).rejects.toThrow(
        status === 403 ? "Permission denied" : "Refresh before continuing",
      );
    },
  );
});

describe("human result and appeal boundaries", () => {
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });
  it("requires exact pending review checksum and latest version before offering approval", () => {
    expect(resultActions(version, true, ["result.review"])).toEqual({
      requestReview: true,
      approve: false,
      release: false,
    });
    const pending = {
      ...version,
      review: {
        id: "review-a",
        status: "PENDING",
        reviewed_checksum: version.checksum,
      },
    };
    expect(resultActions(pending, true, ["result.review"]).approve).toBe(true);
    expect(
      resultActions({ ...pending, checksum: "changed" }, true, [
        "result.review",
      ]).approve,
    ).toBe(false);
    expect(
      resultActions(pending, false, ["result.review", "result.release"]),
    ).toEqual({ requestReview: false, approve: false, release: false });
    expect(
      resultActions({ ...version, status: "APPROVED" }, true, [
        "result.release",
      ]).release,
    ).toBe(true);
    expect(resultActions(version, true, ["result.release"]).release).toBe(
      false,
    );
  });
  it("pins rejection to exact appeal version/checksum/evidence and preserves idempotency identity", async () => {
    setSessionToken("reviewer-bearer");
    setOrganisationContext("org-a");
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(
        json({ id: "appeal-a", status: "ADJUDICATED_REJECTED" }),
      );
    vi.stubGlobal("fetch", fetch);
    await appealApi.reject(
      "org-a",
      appeal,
      "The exact response was scored correctly.",
      "operation-a",
    );
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/appeals/appeal-a/adjudicate",
    );
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      appeal_version_number: 2,
      appeal_checksum: appeal.current_checksum,
      evidence_bundle_checksum: appeal.evidence_bundle_checksum,
      decision: "REJECT",
      reason: "The exact response was scored correctly.",
      operation_id: "operation-a",
    });
    expect(appealActions(appeal, ["appeal.read"]).reject).toBe(false);
    expect(appealActions(appeal, ["appeal.adjudicate"]).reject).toBe(true);
    expect(
      appealActions({ ...appeal, status: "ADJUDICATED_REJECTED" }, [
        "appeal.adjudicate",
      ]).reject,
    ).toBe(false);
  });
  it("uses candidate owner-scoped history/detail and exact released-version appeal submission", async () => {
    setSessionToken("candidate-bearer");
    setOrganisationContext("org-a");
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({}));
    vi.stubGlobal("fetch", fetch);
    await appealApi.candidateList();
    await appealApi.candidateDetail("appeal-a");
    await appealApi.submit(
      "version-a",
      "SCORING",
      "Please inspect my exact response.",
      "operation-a",
    );
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/candidate/appeals",
      "/api/v1/candidate/appeals/appeal-a",
      "/api/v1/candidate/results/version-a/appeals",
    ]);
    expect(JSON.parse(fetch.mock.calls[2][1]?.body as string)).toMatchObject({
      operation_id: "operation-a",
    });
  });
  it("displays only the score pinned by release detail and never substitutes a different/latest version", () => {
    const release = {
      result_version_id: "r1",
      result: {
        result_version_id: "r1",
        raw_awarded_points: "1",
        raw_maximum_points: "1",
        outcome: "UNDETERMINED",
        result_checksum: "checksum-r1",
      },
    } as ReleaseDetail;
    expect(exactReleasedScore(release)?.raw_awarded_points).toBe("1");
    expect(
      exactReleasedScore({
        ...release,
        result: { ...release.result!, result_version_id: "r2" },
      }),
    ).toBeNull();
    expect(
      exactReleasedScore({ result_version_id: "r1" } as ReleaseDetail),
    ).toBeNull();
  });
});

describe("institution feature routing and loading", () => {
  const member: Membership = {
    organisation_id: "org-a",
    organisation_name: "School",
    status: "ACTIVE",
    role_keys: ["EXAMINER"],
    permissions: ["organisation.read"],
  };
  it("hides assignment and result management from candidate roles without actual permissions", () => {
    expect(canViewRoute("/institution/assignments", member)).toBe(false);
    expect(canViewRoute("/institution/results/result-a", member)).toBe(false);
    expect(
      canViewRoute("/institution/assignments", {
        ...member,
        permissions: ["exam_assignment.manage", "exam.read", "membership.read"],
      }),
    ).toBe(true);
    expect(
      canViewRoute("/institution/results/result-a", {
        ...member,
        permissions: ["result.read"],
      }),
    ).toBe(true);
  });
  it("renders useful permission/loading states without invented candidate or result data", () => {
    permissions = [];
    expect(renderToStaticMarkup(<AssignAssessmentPage />)).toContain(
      "Assignment management, exam read and membership read permissions",
    );
    permissions = ["exam_assignment.manage", "exam.read", "membership.read"];
    expect(renderToStaticMarkup(<AssignAssessmentPage />)).toContain(
      "Loading active candidates and deliverable exams",
    );
    permissions = ["result.read"];
    expect(
      renderToStaticMarkup(
        <ResultReviewPage params={Promise.resolve({ resultId: "result-a" })} />,
      ),
    ).toContain("Loading persisted result versions");
  });
});
