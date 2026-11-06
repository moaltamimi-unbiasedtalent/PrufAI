import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canViewRoute, type Membership } from "./lib/access";
import { developmentDemoEnabled } from "./lib/demo";

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function pauseCookieOperation(stage: "lock" | "csrf") {
  let unlock!: () => void;
  let finishCsrf!: (response: Response) => void;
  const fetch = vi.fn<typeof globalThis.fetch>(
    () =>
      new Promise<Response>((resolve) => {
        finishCsrf = resolve;
      }),
  );
  const request = vi.fn((_name: string, operation: () => Promise<unknown>) => {
    if (stage === "csrf") return operation();
    return new Promise((resolve, reject) => {
      unlock = () => {
        void operation().then(resolve, reject);
      };
    });
  });
  vi.stubGlobal("navigator", { locks: { request } });
  vi.stubGlobal("fetch", fetch);
  return {
    fetch,
    resume: () => {
      if (stage === "lock") unlock();
      else finishCsrf(json({ csrf_token: "new-account-cookie" }));
    },
  };
}

describe("same-origin session contract", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.unstubAllGlobals());

  it("refreshes one expired bearer and retries PUT with the same body and tenant", async () => {
    const api = await import("./lib/api");
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ csrf_token: "csrf-a" }))
      .mockResolvedValueOnce(json({ access_token: "fresh" }))
      .mockResolvedValueOnce(json({ revision: 2 }));
    vi.stubGlobal("fetch", fetch);
    api.setSessionToken("expired");
    api.setOrganisationContext("org-a");
    const response = await api.authenticatedFetch(
      "/api/v1/candidate/exam-attempts/1/responses/b",
      { method: "PUT", body: '{"expected_revision":1}' },
    );
    expect(response.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(4);
    const retry = fetch.mock.calls[3];
    expect(retry[0]).toBe("/api/v1/candidate/exam-attempts/1/responses/b");
    expect(retry[1].method).toBe("PUT");
    expect(retry[1].body).toBe('{"expected_revision":1}');
    expect(retry[1].credentials).toBe("same-origin");
    expect(retry[1].headers.get("Authorization")).toBe("Bearer fresh");
    expect(retry[1].headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
    expect(fetch.mock.calls[2][1].headers["X-PrufAI-CSRF"]).toBe("csrf-a");
  });

  it("coalesces concurrent restoration and keeps tokens only in memory", async () => {
    const api = await import("./lib/api");
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ csrf_token: "csrf" }))
      .mockResolvedValueOnce(json({ access_token: "restored" }));
    vi.stubGlobal("fetch", fetch);
    expect(
      await Promise.all([api.refreshAccessToken(), api.refreshAccessToken()]),
    ).toEqual([true, true]);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(api.accessToken()).toBe("restored");
    api.setSessionToken(null);
    expect(api.accessToken()).toBeNull();
  });

  it("invalidates the session when refresh is rejected", async () => {
    const api = await import("./lib/api");
    const expired = vi.fn();
    api.onSessionExpired(expired);
    api.setSessionToken("old");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({}, 401))
        .mockResolvedValueOnce(json({}, 401)),
    );
    expect((await api.authenticatedFetch("/api/v1/auth/me")).status).toBe(401);
    expect(api.accessToken()).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
  });

  it("never retries an old account request using the new account token", async () => {
    const api = await import("./lib/api");
    let complete!: (response: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          complete = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    api.setSessionToken("candidate-a");
    const pending = api.authenticatedFetch(
      "/api/v1/candidate/exam-assignments",
    );
    api.setSessionToken("candidate-b");
    complete(json({}, 401));
    await expect(pending).rejects.toThrow("account or organisation changed");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("uses the rotated double-submit value when logout retries after expiration", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("expired");
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json({ csrf_token: "initial-cookie" }))
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ csrf_token: "old-cookie" }))
      .mockResolvedValueOnce(json({ access_token: "fresh" }))
      .mockResolvedValueOnce(json({ csrf_token: "rotated-cookie" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    expect(
      (
        await api.authenticatedFetch("/api/v1/auth/logout", {
          method: "POST",
          headers: { "X-PrufAI-CSRF": "old-cookie" },
        })
      ).status,
    ).toBe(204);
    expect(fetch.mock.calls[5][1].headers.get("X-PrufAI-CSRF")).toBe(
      "rotated-cookie",
    );
  });

  it("does not bind session identity requests to a stale selected organisation", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("valid");
    api.setOrganisationContext("revoked-org");
    const fetch = vi.fn().mockResolvedValue(json({ id: "user" }));
    vi.stubGlobal("fetch", fetch);
    await api.authenticatedFetch("/api/v1/auth/me");
    expect(fetch.mock.calls[0][1].headers.has("X-PrufAI-Organisation-ID")).toBe(
      false,
    );
  });

  it("keeps platform governance unscoped and institution feedback scoped", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("valid");
    api.setOrganisationContext("org-a");
    const fetch = vi.fn().mockResolvedValue(json({}));
    vi.stubGlobal("fetch", fetch);
    const headers = { "X-PrufAI-Organisation-ID": "stale" };
    const paths = [
      "/api/v1/platform/feedback/items",
      "/api/v1/platform/standards/catalogue/cefr-companion-2020/register",
      "/api/v1/platform/standard-packs/cefr-b1-foundation",
      "/api/v1/platform/standard-pack-versions/example/validate",
    ];
    for (const path of paths) {
      await api.authenticatedFetch(path, { method: "POST", headers });
    }
    await api.authenticatedFetch("/api/v1/organisations/org-a/feedback", {
      method: "POST",
      headers,
    });
    for (const [, request] of fetch.mock.calls.slice(0, paths.length)) {
      expect(request.headers.has("X-PrufAI-Organisation-ID")).toBe(false);
    }
    expect(
      fetch.mock.calls[paths.length][1].headers.get("X-PrufAI-Organisation-ID"),
    ).toBe("org-a");
  });

  it("coordinates cookie rotation through the browser's same-origin lock", async () => {
    const api = await import("./lib/api");
    const request = vi.fn(
      async (_name: string, callback: () => Promise<boolean>) => callback(),
    );
    vi.stubGlobal("navigator", { locks: { request } });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({ csrf_token: "csrf" }))
        .mockResolvedValueOnce(json({ access_token: "fresh" })),
    );
    expect(await api.refreshAccessToken()).toBe(true);
    expect(request).toHaveBeenCalledWith(
      "prufai-session-cookie",
      expect.any(Function),
    );
  });

  it.each(["lock", "csrf"] as const)(
    "cancels logout if the account changes while waiting for %s",
    async (stage) => {
      const api = await import("./lib/api");
      api.setSessionToken("candidate-a");
      const race = pauseCookieOperation(stage);
      const pending = api.authenticatedFetch("/api/v1/auth/logout", {
        method: "POST",
      });
      const rejected = expect(pending).rejects.toThrow(
        "account or organisation changed",
      );
      api.setSessionToken("candidate-b");
      race.resume();
      await rejected;
      expect(api.accessToken()).toBe("candidate-b");
      expect(race.fetch.mock.calls.map(([path]) => path)).toEqual(
        stage === "lock" ? [] : ["/api/v1/auth/csrf"],
      );
    },
  );

  it.each(["lock", "csrf"] as const)(
    "does not rotate a refresh cookie if the account changes while waiting for %s",
    async (stage) => {
      const api = await import("./lib/api");
      api.setSessionToken("candidate-a");
      const race = pauseCookieOperation(stage);
      const pending = api.refreshAccessToken();
      api.setSessionToken("candidate-b");
      race.resume();
      expect(await pending).toBe(false);
      expect(api.accessToken()).toBe("candidate-b");
      expect(race.fetch.mock.calls.map(([path]) => path)).toEqual(
        stage === "lock" ? [] : ["/api/v1/auth/csrf"],
      );
    },
  );

  it("preserves binary audio finalization headers and never sends credentials off-origin", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("candidate");
    const fetch = vi.fn().mockResolvedValue(json({}));
    vi.stubGlobal("fetch", fetch);
    const body = new Blob(["audio"], { type: "audio/ogg" });
    await api.authenticatedFetch(
      "/api/v1/candidate/audio-recordings/1/finalize",
      {
        method: "POST",
        body,
        headers: { "Content-Type": "audio/ogg", "X-Filename": "recording.ogg" },
      },
    );
    expect(fetch.mock.calls[0][1].headers.get("X-Filename")).toBe(
      "recording.ogg",
    );
    expect(fetch.mock.calls[0][1].body).toBe(body);
    await expect(
      api.authenticatedFetch("https://elsewhere.invalid/api/v1/auth/me"),
    ).rejects.toThrow("same-origin");
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe("workspace boundaries", () => {
  const member: Membership = {
    organisation_id: "o",
    organisation_name: "School",
    status: "ACTIVE",
    role_keys: ["EXAMINER"],
    permissions: ["organisation.read"],
  };
  it("uses active membership permissions and candidate role for navigation", () => {
    expect(canViewRoute("/candidate", member)).toBe(true);
    expect(canViewRoute("/institution/overview", member)).toBe(false);
    expect(
      canViewRoute("/institution/overview", {
        ...member,
        role_keys: ["EXAM_MANAGER"],
        permissions: ["exam_assignment.read"],
      }),
    ).toBe(true);
    expect(
      canViewRoute("/institution/overview", {
        ...member,
        status: "SUSPENDED",
        permissions: ["exam_assignment.read"],
      }),
    ).toBe(false);
    expect(canViewRoute("/institution/unknown", member)).toBe(false);
  });
  it("never enables demo helpers in production or without the explicit flag", () => {
    expect(developmentDemoEnabled("production", "1")).toBe(false);
    expect(developmentDemoEnabled("development", undefined)).toBe(false);
    expect(developmentDemoEnabled("development", "1")).toBe(true);
  });
});
