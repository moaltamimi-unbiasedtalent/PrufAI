import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CandidateExamRuntimePage from "./app/candidate/exams/[attemptId]/page";
import {
  RuntimeTimer,
  applySavedResponse,
  crossedTimerMilestone,
  formatRemaining,
  runtimeStateMessage,
} from "./app/candidate/exams/[attemptId]/runtime-ux";
import type { RuntimeState } from "./app/candidate/api";

vi.mock("next/navigation", () => ({
  useParams: () => ({ attemptId: "attempt-123" }),
  useRouter: () => ({ replace: vi.fn() }),
}));

describe("candidate runtime accessibility", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders a stable, labelled timer without per-second live announcements", () => {
    const markup = renderToStaticMarkup(
      <RuntimeTimer monotonicDeadline={null} />,
    );
    expect(markup).toContain("Remaining time: waiting for the server deadline");
    expect(markup).toContain('aria-live="polite"');
    expect(markup).not.toContain("every second");
    expect(formatRemaining(3661)).toBe("01:01:01");
  });

  it("selects the nearest crossed milestone without repeating an elapsed threshold", () => {
    expect(crossedTimerMilestone(301, 298)).toBe("5 minutes remaining");
    expect(crossedTimerMilestone(298, 296)).toBeUndefined();
    expect(crossedTimerMilestone(601, 58)).toBe("1 minute remaining");
    expect(crossedTimerMilestone(61, 0)).toBe(
      "Time is up. Checking the server status.",
    );
    expect(crossedTimerMilestone(0, 0)).toBeUndefined();
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
    expect(runtimeStateMessage("Save failed")).toContain("not confirmed");
  });

  it("inserts first-save revision and replaces it on the second save without duplicates", () => {
    const initial = {
      responses: [],
      status: "IN_PROGRESS",
    } as unknown as RuntimeState;
    const first = applySavedResponse(initial, "binding-1", {
      revision: 1,
      payload: { option_id: "a" },
    });
    expect(first.responses).toEqual([
      { binding_id: "binding-1", revision: 1, payload: { option_id: "a" } },
    ]);
    const second = applySavedResponse(first, "binding-1", {
      revision: 2,
      payload: { option_id: "b" },
    });
    expect(second.responses).toHaveLength(1);
    expect(second.responses[0].revision).toBe(2);
    expect(second.responses[0].payload).toEqual({ option_id: "b" });
    expect(initial.responses).toEqual([]);
    const another = applySavedResponse(second, "binding-2", {
      revision: 1,
      payload: { text: "answer" },
    });
    expect(another.responses.map((item) => item.revision)).toEqual([2, 1]);
  });
});
