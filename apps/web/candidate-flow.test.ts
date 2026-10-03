import { afterEach, describe, expect, it, vi } from "vitest";

import { candidateApi, type RuntimeState } from "./app/candidate/api";

const runtime = (status: string): RuntimeState => ({
  attempt_id: "attempt-1",
  status,
  server_now: "2026-10-03T10:00:00Z",
  started_at: status === "IN_PROGRESS" ? "2026-10-03T10:00:00Z" : null,
  deadline_at: status === "IN_PROGRESS" ? "2026-10-03T11:00:00Z" : null,
  runtime_plan_checksum: "checksum",
  runtime_plan: {
    duration_seconds: 3600,
    modules: [
      {
        module_id: "module-1",
        sections: [
          {
            section_id: "section-1",
            navigation_mode: "LINEAR",
            tasks: [
              {
                task_id: "task-1",
                response_mode: "TEXT",
                navigation_mode: "LINEAR",
                items: [
                  {
                    binding_id: "binding-1",
                    response_mode: "TEXT",
                    content: { prompt: "Safe prompt" },
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
  session: { current_binding_id: "binding-1" },
  responses: [],
});

describe("candidate API flow boundary", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("follows assignment → pre-start → start → save → navigate → submit", async () => {
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    const responses = [
      [
        {
          id: "assignment-1",
          status: "ASSIGNED",
          available_from: null,
          available_until: null,
          maximum_attempts: 1,
          attempts: [],
        },
      ],
      {
        id: "attempt-1",
        status: "READY",
        attempt_number: 1,
        started_at: null,
        deadline_at: null,
        submitted_at: null,
        expired_at: null,
        runtime_plan_checksum: "checksum",
      },
      runtime("READY"),
      runtime("IN_PROGRESS"),
      { response_id: "response-1", revision: 1, payload: { text: "answer" } },
      { current_binding_id: "binding-1" },
      {
        id: "attempt-1",
        status: "SUBMITTED",
        attempt_number: 1,
        started_at: "2026-10-03T10:00:00Z",
        deadline_at: "2026-10-03T11:00:00Z",
        submitted_at: "2026-10-03T10:10:00Z",
        expired_at: null,
        runtime_plan_checksum: "checksum",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({
          url: String(input),
          method: init?.method ?? "GET",
          body: init?.body as string | undefined,
        });
        return new Response(JSON.stringify(responses.shift()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const assignments = await candidateApi.assignments();
    expect(assignments[0].status).toBe("ASSIGNED");
    await candidateApi.createAttempt("assignment-1");
    await candidateApi.runtime("attempt-1");
    await candidateApi.start("attempt-1");
    await candidateApi.save(
      "attempt-1",
      "binding-1",
      { text: "answer" },
      0,
      "operation-1",
    );
    await candidateApi.navigate("attempt-1", "binding-1");
    await candidateApi.submit("attempt-1");

    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      "GET /api/v1/candidate/exam-assignments",
      "POST /api/v1/candidate/exam-assignments/assignment-1/attempts",
      "GET /api/v1/candidate/exam-attempts/attempt-1/runtime",
      "POST /api/v1/candidate/exam-attempts/attempt-1/start",
      "PUT /api/v1/candidate/exam-attempts/attempt-1/responses/binding-1",
      "POST /api/v1/candidate/exam-attempts/attempt-1/navigate?binding_id=binding-1",
      "POST /api/v1/candidate/exam-attempts/attempt-1/submit",
    ]);
    expect(calls[4].body).toContain("operation-1");
  });
});
