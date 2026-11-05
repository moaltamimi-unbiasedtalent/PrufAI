import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import FeedbackWorkspace, {
  ProposalDetails,
} from "./app/control-center/feedback/feedback-workspace";
import WritingFeedbackPanel from "./app/institution/writing-reviews/feedback-panel";
import type { Proposal } from "./app/control-center/feedback/feedback-api";

const auth = vi.hoisted(() => ({
  user: {
    id: "limited",
    platform_permissions: ["platform.control_center.read"],
  },
  organisationId: "tenant-a",
  permissions: ["subjective.read"],
  loading: false,
}));
vi.mock("./lib/auth", () => ({ useAuth: () => auth }));

const pin = {
  id: "e64819b1-951b-4663-90ac-a41b8473c912",
  checksum: "a".repeat(64),
};

it("does not expose feedback forms through platform entry or subjective read alone", () => {
  const platform = renderToStaticMarkup(<FeedbackWorkspace />);
  expect(platform).toContain(
    "Platform evaluation read permission is required.",
  );
  expect(platform).not.toContain("Record feedback");
  expect(
    renderToStaticMarkup(
      <WritingFeedbackPanel artifact={pin} status="APPROVED" />,
    ),
  ).toBe("");
});

it("shows an independently approved proposal as requiring evaluation with release not ready", () => {
  const proposal: Proposal = {
    ...pin,
    proposal_id: pin.id,
    version_number: 1,
    pattern: pin,
    target: {
      ...pin,
      type: "STANDARD_PACK_VERSION",
      version_label: "published-v1",
    },
    receipt: pin,
    reviewed_by: "independent-reviewer",
    change_category: "VALIDATION_GUARD",
    hypothesis_code: "PREVENT_RECORDED_FAILURE",
    status: "APPROVED_FOR_EVALUATION",
    evaluation_requirements: {
      status: "REQUIRE_EVALUATION",
      required_checks: ["criterion_coverage"],
      reference_evidence: "REQUIRE_APPROVED_REFERENCE",
    },
    links: { quality_gate: "NOT_EVALUATED" },
    release_readiness: "NOT_READY",
    allowed_actions: [],
    decision_reason_code: "EVALUATION_PLAN_SUFFICIENT",
    production_activation: false,
  };
  const view = renderToStaticMarkup(<ProposalDetails proposal={proposal} />);
  for (const value of [
    pin.checksum,
    "APPROVED_FOR_EVALUATION",
    "REQUIRE_EVALUATION",
    "REQUIRE_APPROVED_REFERENCE",
    "criterion_coverage",
    "No approved suite linked",
    "NOT_EVALUATED",
    "NOT_READY",
    "independent-reviewer",
  ])
    expect(view).toContain(value);
});
