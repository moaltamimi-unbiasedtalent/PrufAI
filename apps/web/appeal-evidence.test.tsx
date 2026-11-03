import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setOrganisationContext, setSessionToken } from "./lib/api";
import {
  appealApi,
  appealEvidenceComplete,
  canInspectAppealEvidence,
  type AppealDetail,
  type AppealEvidence,
} from "./app/institution/appeals/appeal-api";

const permissions = [
  "appeal.read",
  "appeal.adjudicate",
  "result.read",
  "question_bank.read",
];
const appeal = {
  id: "appeal-r1",
  result_id: "result-a",
  challenged_result_version_id: "version-r1",
  challenged_result_checksum: "r".repeat(64),
  evidence_bundle_checksum: "e".repeat(64),
} as AppealDetail;
const evidence: AppealEvidence = {
  appeal_id: appeal.id,
  result_id: appeal.result_id,
  result_version_id: appeal.challenged_result_version_id,
  result_checksum: appeal.challenged_result_checksum,
  scoring_run_id: "run-s1",
  scoring_run_checksum: "s".repeat(64),
  exam_version_id: "exam-v1",
  standard_pack_version_id: "pack-v1",
  evidence_bundle_checksum: appeal.evidence_bundle_checksum!,
  pinned_evidence_bundle_checksum: appeal.evidence_bundle_checksum!,
  matches_pinned_bundle: true,
  total_items: 1,
  limit: 50,
  unsupported_items: 0,
  items: [
    {
      binding_id: "binding-original",
      item_version_id: "item-v1",
      item_checksum: "i".repeat(64),
      response_id: "response-original",
      response_revision_id: "revision-2",
      response_revision_number: 2,
      response_checksum: "a".repeat(64),
      prompt: "Original synthetic question: Wann beginnt der Kurs?",
      options: [
        { option_id: "a", display_text: "Um 9 Uhr." },
        { option_id: "b", display_text: "Um 10 Uhr." },
      ],
      response: { option_id: "a" },
      correct_option_ids: ["a"],
      scoring_policy: { method: "ALL_OR_NOTHING", version: "1" },
      awarded_points: "1",
      maximum_points: "1",
      status: "SCORED",
    },
  ],
};

describe("authorized original appeal evidence", () => {
  beforeEach(() => {
    setSessionToken("reviewer-bearer");
    setOrganisationContext("org-a");
  });
  afterEach(() => {
    setSessionToken(null);
    vi.unstubAllGlobals();
  });

  it("loads the exact appeal evidence endpoint with existing bearer and tenant context, without latest item substitution", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify(evidence), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const actual = await appealApi.evidence("org-a", appeal.id);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organisations/org-a/appeals/appeal-r1/evidence",
    );
    const headers = new Headers(fetch.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer reviewer-bearer");
    expect(headers.get("X-PrufAI-Organisation-ID")).toBe("org-a");
    expect(actual.items[0].item_version_id).toBe("item-v1");
    expect(actual.items[0].response_revision_id).toBe("revision-2");
  });

  it.each([403, 409, 503])(
    "preserves HTTP %s evidence failure for denial/conflict/outage retry",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            new Response(
              JSON.stringify({ detail: "Evidence is not available" }),
              { status, headers: { "Content-Type": "application/json" } },
            ),
          ),
      );
      await expect(
        appealApi.evidence("org-a", appeal.id),
      ).rejects.toMatchObject({ status });
    },
  );

  it("requires every actual evidence permission, rather than membership or role labels", () => {
    expect(canInspectAppealEvidence(permissions)).toBe(true);
    for (const missing of permissions)
      expect(
        canInspectAppealEvidence(permissions.filter((key) => key !== missing)),
      ).toBe(false);
    expect(
      canInspectAppealEvidence(["INSTITUTION_ADMIN", "organisation.read"]),
    ).toBe(false);
  });

  it("allows adjudication only after complete evidence matches exact appeal/result/bundle pins", () => {
    expect(appealEvidenceComplete(appeal, evidence)).toBe(true);
    expect(appealEvidenceComplete(appeal, null)).toBe(false);
    for (const mutation of [
      { appeal_id: "another-appeal" },
      { result_id: "another-result" },
      { result_version_id: "latest-r2" },
      { result_checksum: "changed" },
      { matches_pinned_bundle: false },
      { evidence_bundle_checksum: "new-bundle" },
      { pinned_evidence_bundle_checksum: "other-pin" },
      { unsupported_items: 1 },
      { truncated: true },
      { items: [{ ...evidence.items[0], content_truncated: true }] },
      { total_items: 51 },
      { items: [] },
    ]) {
      expect(appealEvidenceComplete(appeal, { ...evidence, ...mutation })).toBe(
        false,
      );
    }
  });
});
