import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canViewRoute, type Membership } from "./lib/access";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import {
  releaseActions,
  releaseApi,
  releaseStateMessage,
} from "./app/institution/releases/release-api";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("authenticated result-release interface", () => {
  beforeEach(() => {
    setSessionToken("reviewer-bearer");
    setOrganisationContext("org-a");
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });

  it("loads institution release history using the shared bearer and tenant contract", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(
        json([
          { id: "release-a", status: "REVOKED", effective_status: "REVOKED" },
        ]),
      );
    vi.stubGlobal("fetch", fetch);
    const releases = await releaseApi.list("org-a");
    expect(releases[0].status).toBe("REVOKED");
    const [path, init] = fetch.mock.calls[0];
    expect(path).toBe("/api/v1/organisations/org-a/releases");
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer reviewer-bearer",
    );
    expect(new Headers(init?.headers).get("X-PrufAI-Organisation-ID")).toBe(
      "org-a",
    );
    expect(init?.credentials).toBe("same-origin");
  });

  it("pins the requested result version, operation and predecessor on the authoritative release route", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(
        json({ id: "result-v2", release_id: "release-v2", status: "RELEASED" }),
      );
    vi.stubGlobal("fetch", fetch);
    const result = await releaseApi.release(
      "org-a",
      "result-v2",
      "operation-a",
      "release-v1",
    );
    expect(result.release_id).toBe("release-v2");
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/result-versions/result-v2/release",
    );
    expect(fetch.mock.calls[0][1]?.method).toBe("POST");
    expect(JSON.parse(fetch.mock.calls[0][1]?.body as string)).toEqual({
      operation_id: "operation-a",
      supersedes_release_id: "release-v1",
    });
  });

  it("derives a queue from actual permitted assignment results rather than invented releases", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json([
          {
            id: "assignment-a",
            candidate_name: "Candidate A",
            exam_name: "Original practice",
            attempts: [
              {
                id: "attempt-a",
                result: {
                  id: "result-a",
                  version_id: "version-a",
                  status: "APPROVED",
                  checksum: "checksum-a",
                },
              },
            ],
          },
          {
            id: "assignment-b",
            candidate_name: "Candidate B",
            exam_name: "Original practice",
            attempts: [{ id: "attempt-b", result: null }],
          },
        ]),
      ),
    );
    expect(await releaseApi.queue("org-a")).toEqual([
      {
        assignment_id: "assignment-a",
        candidate_name: "Candidate A",
        exam_name: "Original practice",
        attempt_id: "attempt-a",
        result_id: "result-a",
        version_id: "version-a",
        status: "APPROVED",
        checksum: "checksum-a",
      },
    ]);
  });

  it("reads candidate detail and statement only through the owner-scoped authenticated endpoints", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(
        json({
          id: "release-a",
          result_version_id: "version-a",
          status: "SUPERSEDED",
        }),
      )
      .mockResolvedValueOnce(
        json({
          id: "statement-a",
          status: "ISSUED",
          effective_status: "SUPERSEDED",
          payload: {},
        }),
      );
    vi.stubGlobal("fetch", fetch);
    await releaseApi.candidateDetail("release-a");
    const statement = await releaseApi.candidateStatement("release-a");
    expect(fetch.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/candidate/releases/release-a",
      "/api/v1/candidate/releases/release-a/statement",
    ]);
    expect(statement.effective_status).toBe("SUPERSEDED");
    expect(
      new Headers(fetch.mock.calls[1][1]?.headers).get("Authorization"),
    ).toBe("Bearer reviewer-bearer");
  });

  it("preserves authoritative permission and blocked-state errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ detail: "release is not active" }, 409)),
    );
    await expect(
      releaseApi.issueStatement("org-a", "revoked", "op"),
    ).rejects.toMatchObject({ status: 409, message: "release is not active" });
  });
});

describe("release navigation and action eligibility", () => {
  const member: Membership = {
    organisation_id: "org-a",
    organisation_name: "School",
    status: "ACTIVE",
    role_keys: ["EXAMINER"],
    permissions: ["organisation.read"],
  };
  it("exposes candidate statement navigation only through candidate membership and institution history through result.read", () => {
    expect(canViewRoute("/candidate/results/releases/a", member)).toBe(true);
    expect(canViewRoute("/institution/releases", member)).toBe(false);
    expect(
      canViewRoute("/institution/releases/a", {
        ...member,
        permissions: ["result.read"],
      }),
    ).toBe(true);
    expect(
      canViewRoute("/institution/releases/a", {
        ...member,
        status: "SUSPENDED",
        permissions: ["result.read"],
      }),
    ).toBe(false);
  });
  it("never offers statement issuance for revoked, superseded or blocked releases", () => {
    expect(releaseActions("RELEASED", ["result.release"])).toEqual({
      issueStatement: true,
      revoke: false,
    });
    expect(releaseActions("RELEASED", ["result.manage"])).toEqual({
      issueStatement: false,
      revoke: true,
    });
    for (const status of ["REVOKED", "SUPERSEDED", "BLOCKED"]) {
      expect(
        releaseActions(status, ["result.release", "result.manage"]),
      ).toEqual({ issueStatement: false, revoke: false });
      expect(releaseStateMessage(status)).not.toContain("Current release.");
    }
  });
});
