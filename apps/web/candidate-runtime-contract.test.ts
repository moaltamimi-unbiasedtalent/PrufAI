import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidateApiError, candidateApi } from "./app/candidate/api";
import { runtimeStateMessage } from "./app/candidate/exams/[attemptId]/runtime-ux";

function mockError(status: number, detail: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail }), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

describe("candidate runtime contract errors", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the authoritative location when navigation is rejected", async () => {
    mockError(409, "navigation policy forbids moving backwards");
    const currentBinding = "binding-1";
    await expect(
      candidateApi.navigate("attempt-1", "binding-0"),
    ).rejects.toMatchObject({ status: 409 });
    expect(currentBinding).toBe("binding-1");
  });

  it("normalizes unsupported mode and ownership errors without exposing internals", async () => {
    mockError(400, "RUNTIME.UNSUPPORTED_RESPONSE_MODE:AUDIO");
    await expect(candidateApi.start("attempt-1")).rejects.toMatchObject({
      code: "RUNTIME.UNSUPPORTED_RESPONSE_MODE:AUDIO",
    });
    mockError(403, "candidate ownership required");
    await expect(
      candidateApi.runtime("foreign-attempt"),
    ).rejects.toBeInstanceOf(CandidateApiError);
  });

  it("keeps conflict, expiry and submitted UX states distinct", () => {
    expect(runtimeStateMessage("Conflict")).toContain("newer response");
    expect(runtimeStateMessage("Expired")).toContain("no longer writable");
    expect(runtimeStateMessage("Submitted")).toBeNull();
  });
});
