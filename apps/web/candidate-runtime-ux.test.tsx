import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CandidateExamRuntimePage from "./app/candidate/exams/[attemptId]/page";
import {
  RuntimeTimer,
  formatRemaining,
  runtimeStateMessage,
} from "./app/candidate/exams/[attemptId]/runtime-ux";

vi.mock("next/navigation", () => ({
  useParams: () => ({ attemptId: "attempt-123" }),
}));

describe("candidate runtime accessibility", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders a stable, labelled timer without per-second live announcements", () => {
    const markup = renderToStaticMarkup(<RuntimeTimer deadlineAt={null} />);
    expect(markup).toContain("Remaining time: waiting for the server deadline");
    expect(markup).toContain('aria-live="polite"');
    expect(markup).not.toContain("every second");
    expect(formatRemaining(3661)).toBe("01:01:01");
  });

  it("renders an accessible loading state before runtime data is available", () => {
    const markup = renderToStaticMarkup(<CandidateExamRuntimePage />);
    expect(markup).toContain('role="status"');
    expect(markup).toContain("Loading exam");
    expect(markup).not.toContain("dangerouslySetInnerHTML");
  });

  it("keeps conflict, expiry and submitted states explicit", () => {
    expect(runtimeStateMessage("Conflict")).toContain("newer response");
    expect(runtimeStateMessage("Expired")).toContain("no longer writable");
    expect(runtimeStateMessage("Submitted")).toBeNull();
  });
});
