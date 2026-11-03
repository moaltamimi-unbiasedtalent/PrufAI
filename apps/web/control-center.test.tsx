import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  canViewRoute,
  initialMembership,
  workspaceLanding,
  type DemoUser,
  type Membership,
} from "./lib/access";
import { controlApi, displayMetadata } from "./app/control-center/control-api";
import {
  ErrorNotice,
  MetadataBrowser,
  MetadataTable,
  Pagination,
} from "./app/control-center/metadata";
import { ControlCentreShell } from "./app/control-center/shell";

const platformUser: DemoUser = {
  id: "platform-operator",
  email: "operator@example.test",
  display_name: "Platform operator",
  memberships: [],
  platform_scope: true,
  platform_permissions: [
    "platform.control_center.read",
    "platform.organisation.read",
    "platform.audit.read",
    "standard_pack.read",
    "standards.source.read",
    "question_bank.read",
    "exam.read",
    "ai.read",
    "platform.system.read",
    "certification.read",
  ],
};
let currentUser: DemoUser = platformUser;
const institution: Membership = {
  organisation_id: "school-a",
  organisation_name: "School A",
  status: "ACTIVE",
  role_keys: ["INSTITUTION_ADMIN"],
  permissions: ["exam_assignment.read", "result.read", "question_bank.read"],
};

vi.mock("./lib/auth", () => ({
  useAuth: () => ({
    user: currentUser,
    loading: false,
    membership: currentUser.memberships[0],
    signOut: vi.fn(),
    error: null,
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/control-center",
  useRouter: () => ({ replace: vi.fn() }),
}));

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("platform workspace routing and separation", () => {
  beforeEach(() => {
    currentUser = platformUser;
  });
  afterEach(() => vi.unstubAllGlobals());

  it("routes the real platform permission contract without requiring or inventing a tenant membership", () => {
    expect(workspaceLanding(platformUser)).toBe("/control-center");
    expect(
      canViewRoute(
        "/control-center/audit",
        undefined,
        platformUser.platform_permissions,
      ),
    ).toBe(true);
    expect(
      canViewRoute("/candidate", undefined, platformUser.platform_permissions),
    ).toBe(false);
    expect(
      canViewRoute(
        "/institution/overview",
        undefined,
        platformUser.platform_permissions,
      ),
    ).toBe(false);
  });

  it("never converts institution, reviewer or candidate permissions into platform access", () => {
    for (const roles of [["INSTITUTION_ADMIN"], ["REVIEWER"], ["EXAMINER"]]) {
      const member = {
        ...institution,
        role_keys: roles,
        permissions: [
          ...institution.permissions,
          "platform.control_center.read",
        ],
      };
      expect(canViewRoute("/control-center", member)).toBe(false);
    }
    expect(
      canViewRoute("/control-center", institution, [
        "platform.organisation.read",
      ]),
    ).toBe(false);
    expect(
      workspaceLanding({
        ...platformUser,
        platform_permissions: [],
        memberships: [institution],
      }),
    ).toBe("/institution/overview");
    expect(
      workspaceLanding({
        ...platformUser,
        platform_permissions: [],
        memberships: [
          {
            ...institution,
            role_keys: ["EXAMINER"],
            permissions: ["organisation.read"],
          },
        ],
      }),
    ).toBe("/candidate");
  });

  it("uses the adopted active membership for login landing instead of a different organisation's role", () => {
    const candidate = {
      ...institution,
      organisation_id: "school-b",
      role_keys: ["EXAMINER"],
    };
    const user = {
      ...platformUser,
      platform_permissions: [],
      memberships: [
        { ...candidate, status: "SUSPENDED" },
        institution,
        candidate,
      ],
    };
    expect(initialMembership(user)?.organisation_id).toBe("school-a");
    expect(workspaceLanding(user)).toBe("/institution/overview");
    expect(workspaceLanding(user, candidate)).toBe("/candidate");
  });

  it("renders an operator shell with distinct accessible navigation and no tenant selector or privilege picker", () => {
    const html = renderToStaticMarkup(
      <ControlCentreShell>
        <h1>Platform evidence</h1>
      </ControlCentreShell>,
    );
    expect(html).toContain('aria-label="Platform navigation"');
    expect(html).toContain('aria-current="page"');
    expect(html).toContain("Platform operator");
    expect(html).toContain("Environment not verified");
    expect(html).toContain("Sign out");
    expect(html).not.toContain("<select");
    expect(html).not.toContain("Institution inspection");
    expect(html).not.toContain("LOCAL DEMO");
  });

  it("denies tenant actors before mounting platform children", () => {
    currentUser = {
      ...platformUser,
      platform_permissions: [],
      memberships: [institution],
    };
    const html = renderToStaticMarkup(
      <ControlCentreShell>
        <p>PRIVATE PLATFORM DATA</p>
      </ControlCentreShell>,
    );
    expect(html).toContain("Access denied");
    expect(html).not.toContain("PRIVATE PLATFORM DATA");
    expect(html).not.toContain("Platform navigation");
  });
});

describe("platform API authentication and metadata-only requests", () => {
  beforeEach(() => {
    currentUser = platformUser;
  });
  afterEach(async () => {
    const api = await import("./lib/api");
    api.setSessionToken(null);
    vi.unstubAllGlobals();
  });

  it("strips selected and caller-supplied tenant headers only from Control Centre and auth requests", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("platform-token");
    api.setOrganisationContext("selected-school");
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async () => json({}));
    vi.stubGlobal("fetch", fetch);
    await api.authenticatedFetch("/api/v1/platform/control-center/overview", {
      headers: { "X-PrufAI-Organisation-ID": "forged-school" },
    });
    await api.authenticatedFetch("/api/v1/platform/question-bank/items");
    await api.authenticatedFetch("/api/v1/auth/me");
    const headers = fetch.mock.calls.map(
      ([, init]) => new Headers(init?.headers),
    );
    expect(headers[0].has("X-PrufAI-Organisation-ID")).toBe(false);
    expect(headers[0].get("Authorization")).toBe("Bearer platform-token");
    expect(headers[1].get("X-PrufAI-Organisation-ID")).toBe("selected-school");
    expect(headers[2].has("X-PrufAI-Organisation-ID")).toBe(false);
  });

  it("passes bounded pagination and explicit metadata scope as query parameters without impersonation", async () => {
    const api = await import("./lib/api");
    api.setSessionToken("platform-token");
    api.setOrganisationContext("selected-school");
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      json({
        items: [{ id: "item-a", scope_type: "ORGANISATION_PRIVATE" }],
        total: 1,
        offset: 0,
        limit: 20,
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const result = await controlApi.page("question-bank", {
      organisation_id: "org-a",
      offset: 0,
      limit: 20,
    });
    const url = new URL(String(fetch.mock.calls[0][0]), "http://localhost");
    expect(url.pathname).toBe("/api/v1/platform/control-center/question-bank");
    expect(url.searchParams.get("organisation_id")).toBe("org-a");
    expect(url.searchParams.get("limit")).toBe("20");
    expect(result.items).toEqual([
      { id: "item-a", scope_type: "ORGANISATION_PRIVATE" },
    ]);
    expect(
      new Headers(fetch.mock.calls[0][1]?.headers).has(
        "X-PrufAI-Organisation-ID",
      ),
    ).toBe(false);
    expect(fetch.mock.calls[0][1]?.credentials).toBe("same-origin");
  });

  it("returns a useful forbidden state without exposing arbitrary private backend error payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ detail: "PRIVATE DIAGNOSTIC" }, 403)),
    );
    await expect(controlApi.overview()).rejects.toMatchObject({
      status: 403,
      message: "Your platform permissions do not allow this view.",
    });
  });
});

describe("safe accessible platform metadata", () => {
  beforeEach(() => {
    currentUser = platformUser;
  });
  it("renders only explicit safe columns and escapes metadata as text", () => {
    const html = renderToStaticMarkup(
      <MetadataTable
        caption="Audit events"
        columns={[["event_type", "Event type"]]}
        rows={[
          {
            id: "audit-a",
            event_type: "<script>not executable</script>",
            private_answer: "PRIVATE ANSWER",
            prompt: "PRIVATE PROMPT",
            password: "SECRET",
          },
        ]}
      />,
    );
    expect(html).toContain('scope="col"');
    expect(html).toContain('tabindex="0"');
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("PRIVATE ANSWER");
    expect(html).not.toContain("PRIVATE PROMPT");
    expect(html).not.toContain("SECRET");
  });
  it("shows loading and explicit denial without inventing metadata or counts", () => {
    expect(
      renderToStaticMarkup(<MetadataBrowser section="organisations" />),
    ).toContain("Loading organisations");
    currentUser = {
      ...platformUser,
      platform_permissions: ["platform.control_center.read"],
    };
    const denied = renderToStaticMarkup(
      <MetadataBrowser section="organisations" />,
    );
    expect(denied).toContain("Your platform permissions do not allow");
    expect(denied).not.toContain("Loading organisations");
    expect(displayMetadata(null)).toBe("—");
    expect(displayMetadata(0)).toBe("0");
  });
  it("opens the authorized metadata subtype for narrowly scoped platform operators", () => {
    currentUser = {
      ...platformUser,
      platform_permissions: [
        "platform.control_center.read",
        "standards.authority.read",
        "standards.document.read",
      ],
    };
    const standards = renderToStaticMarkup(
      <MetadataBrowser section="standards" />,
    );
    const documents = renderToStaticMarkup(
      <MetadataBrowser section="source-registry" />,
    );
    expect(standards).toContain("Loading authorities &amp; frameworks");
    expect(standards).not.toContain("Your platform permissions do not allow");
    expect(documents).toContain("Loading document versions &amp; rights");
  });
  it("places organisation metadata links inside the first column where they are visible without horizontal scrolling", () => {
    const html = renderToStaticMarkup(
      <MetadataTable
        caption="Organisations"
        columns={[
          ["name", "Organisation"],
          ["id", "Reference"],
        ]}
        rows={[{ id: "school-a", name: "School A" }]}
        organisationLinks
      />,
    );
    const firstCell = html.slice(html.indexOf("<td>"), html.indexOf("</td>"));
    expect(firstCell).toContain("School A");
    expect(firstCell).toContain("Question metadata");
    expect(firstCell).toContain("Exam metadata");
    expect(firstCell).toContain("organisation_id=school-a");
    expect(html).not.toContain("Metadata links</th>");
  });
  it("provides retry and correctly bounded pagination controls", () => {
    const retry = renderToStaticMarkup(
      <ErrorNotice message="Unavailable" retry={() => undefined} />,
    );
    expect(retry).toContain('role="alert"');
    expect(retry).toContain("Retry loading");
    const page = renderToStaticMarkup(
      <Pagination
        page={{ items: [], total: 0, limit: 20, offset: 0 }}
        setOffset={() => undefined}
      />,
    );
    expect(page).toContain("0 records");
    expect(page.match(/disabled=""/g)).toHaveLength(2);
  });
});
