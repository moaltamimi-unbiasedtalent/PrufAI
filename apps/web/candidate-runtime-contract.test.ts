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

  it("sends writer context without permitting redirected credential-bearing mutations", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            server_now: "2026-11-18T00:00:00Z",
            deadline_at: null,
            generation: 2,
            claimed: true,
            policy: "SINGLE_WRITER",
          }),
          { headers: { "Content-Type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetcher);
    const writer = {
      clientId: "53a4e425-817b-45e0-9140-462ba580089c",
      generation: 2,
    };
    await candidateApi.claimWriter("attempt-1", writer.clientId, 1, true);
    await candidateApi.start("attempt-1", writer);
    await candidateApi.save(
      "attempt-1",
      "binding-1",
      { option_id: "a" },
      1,
      "operation-1",
      undefined,
      writer,
    );
    await candidateApi.navigate("attempt-1", "binding-2", undefined, writer);
    await candidateApi.submit("attempt-1", undefined, writer);
    expect(fetcher).toHaveBeenCalledTimes(5);
    for (const call of fetcher.mock.calls as unknown as Array<
      [string, RequestInit]
    >) {
      expect(call[1].redirect).toBe("error");
    }
    const calls = fetcher.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(JSON.parse(calls[0][1].body as string)).toEqual({
      client_id: writer.clientId,
      expected_generation: 1,
      acknowledge_takeover: true,
    });
    for (const [, init] of calls.slice(1)) {
      const headers = new Headers(init.headers);
      expect(headers.get("X-PrufAI-Writer-ID")).toBe(writer.clientId);
      expect(headers.get("X-PrufAI-Writer-Generation")).toBe("2");
    }
  });

  it("keeps conflict, expiry and submitted UX states distinct", () => {
    expect(runtimeStateMessage("Conflict")).toContain("newer response");
    expect(runtimeStateMessage("Expired")).toContain("no longer writable");
    expect(runtimeStateMessage("Submitted")).toBeNull();
  });
});
