"use client";

import { useParams } from "next/navigation";
import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  CandidateApiError,
  candidateApi,
  type RuntimeState as RuntimeDto,
} from "../../api";
import {
  AudioResponseControl,
  RuntimeTimer,
  runtimeStateMessage,
  type ResponseMode,
  type RuntimeState,
} from "./runtime-ux";
import {
  queueCandidateOperation,
  syncCandidateOutbox,
  type CandidateOutboxOperation,
} from "../../outbox";

type Screen = "loading" | "prestart" | "runtime" | "unavailable";
type RuntimeItem =
  RuntimeDto["runtime_plan"]["modules"][number]["sections"][number]["tasks"][number]["items"][number] & {
    taskId: string;
    navigationMode: string;
  };

const modeLabels: Record<string, string> = {
  SELECT_ONE: "Select one",
  SELECT_MULTIPLE: "Select multiple",
  TEXT: "Short text",
  LONG_TEXT: "Long text",
  AUDIO: "Recorded audio",
};

function flattenItems(runtime: RuntimeDto): RuntimeItem[] {
  return runtime.runtime_plan.modules.flatMap((module) =>
    module.sections.flatMap((section) =>
      section.tasks.flatMap((task) =>
        task.items.map((item) => ({
          ...item,
          taskId: task.task_id,
          navigationMode: task.navigation_mode,
        })),
      ),
    ),
  );
}

function safeError(cause: unknown): string {
  if (!(cause instanceof CandidateApiError))
    return "The runtime is temporarily unavailable.";
  if (cause.code.includes("UNSUPPORTED_RESPONSE_MODE"))
    return "This exam contains a response type that is not available in Candidate Runtime.";
  if (cause.status === 403 || cause.status === 404)
    return "This exam attempt is unavailable to the authenticated candidate.";
  if (cause.code.includes("available") || cause.code.includes("limit"))
    return "This assignment cannot be started right now.";
  if (cause.status === 409)
    return "The server rejected this action because the attempt state changed. Refresh and try again.";
  return "The runtime is temporarily unavailable.";
}

export default function CandidateExamRuntimePage() {
  const params = useParams<{ attemptId: string }>();
  const routeId = params.attemptId;
  const assignmentId = routeId.startsWith("assignment-")
    ? routeId.slice("assignment-".length)
    : null;
  const promptId = useId();
  const responseHelpId = `${promptId}-help`;
  const errorId = `${promptId}-error`;
  const [screen, setScreen] = useState<Screen>("loading");
  const [attemptId, setAttemptId] = useState<string | null>(
    assignmentId ? null : routeId,
  );
  const [runtime, setRuntime] = useState<RuntimeDto | null>(null);
  const [status, setStatus] = useState<RuntimeState>("Saved");
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<Record<string, unknown>>({});
  const [starting, setStarting] = useState(false);
  const questionHeadingRef = useRef<HTMLHeadingElement>(null);

  const items = useMemo(
    () => (runtime ? flattenItems(runtime) : []),
    [runtime],
  );
  const currentBinding =
    runtime?.session.current_binding_id ?? items[0]?.binding_id ?? null;
  const currentItem =
    items.find((item) => item.binding_id === currentBinding) ??
    items[0] ??
    null;
  const currentIndex = currentItem
    ? items.findIndex((item) => item.binding_id === currentItem.binding_id)
    : -1;
  const editable = runtime?.status === "IN_PROGRESS";

  async function loadRuntime(id: string, nextScreen?: Screen) {
    const state = await candidateApi.runtime(id);
    setAttemptId(id);
    setRuntime(state);
    const persisted = state.responses.find(
      (item) => item.binding_id === (state.session.current_binding_id ?? ""),
    );
    setResponse(persisted?.payload ?? {});
    setScreen(
      nextScreen ??
        (state.status === "READY"
          ? "prestart"
          : state.status === "IN_PROGRESS" ||
              state.status === "SUBMITTED" ||
              state.status === "EXPIRED"
            ? "runtime"
            : "unavailable"),
    );
  }

  useEffect(() => {
    let active = true;
    if (assignmentId) {
      candidateApi
        .assignments()
        .then((rows) => {
          if (!active && rows.length === 0) return;
          if (!rows.some((row) => row.id === assignmentId))
            throw new CandidateApiError(404, "assignment unavailable");
          setScreen("prestart");
        })
        .catch((cause: unknown) => {
          if (active) {
            setError(safeError(cause));
            setScreen("unavailable");
          }
        });
    } else {
      loadRuntime(routeId).catch((cause: unknown) => {
        if (active) {
          setError(safeError(cause));
          setScreen("unavailable");
        }
      });
    }
    return () => {
      active = false;
    };
  }, [assignmentId, routeId]);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  useEffect(() => {
    if (!attemptId || !editable) return;
    const heartbeat = window.setInterval(() => {
      void candidateApi
        .heartbeat(attemptId)
        .then((state) => {
          setRuntime((current) =>
            current
              ? {
                  ...current,
                  status: state.status,
                  started_at: state.started_at,
                  deadline_at: state.deadline_at,
                }
              : current,
          );
          if (state.status === "EXPIRED") setStatus("Expired");
        })
        .catch(() => undefined);
    }, 30000);
    return () => window.clearInterval(heartbeat);
  }, [attemptId, editable]);

  useEffect(() => {
    if (!online || !attemptId) return;
    void syncCandidateOutbox(async (operation: CandidateOutboxOperation) => {
      try {
        await candidateApi.save(
          operation.attemptId,
          operation.bindingId,
          operation.payload,
          operation.expectedRevision,
          operation.operationId,
        );
        setStatus("Saved");
        return "ack";
      } catch (cause) {
        if (cause instanceof CandidateApiError && cause.status === 409) {
          setStatus("Conflict");
          return "conflict";
        }
        if (
          cause instanceof CandidateApiError &&
          cause.code.includes("expired")
        ) {
          setStatus("Expired");
          return "expired";
        }
        return "conflict";
      }
    });
  }, [online, attemptId]);

  async function prepareAttempt() {
    if (!assignmentId) return;
    setStarting(true);
    setError(null);
    try {
      const attempt = await candidateApi.createAttempt(assignmentId);
      await loadRuntime(attempt.id, "prestart");
    } catch (cause) {
      setError(safeError(cause));
      setScreen("prestart");
    } finally {
      setStarting(false);
    }
  }

  async function startAttempt() {
    if (!attemptId) return;
    setStarting(true);
    setError(null);
    try {
      const started = await candidateApi.start(attemptId);
      setRuntime(started);
      setScreen("runtime");
    } catch (cause) {
      setError(safeError(cause));
    } finally {
      setStarting(false);
    }
  }

  async function persist(payload: Record<string, unknown>) {
    if (!attemptId || !currentItem || !editable) return;
    const existing = runtime?.responses.find(
      (item) => item.binding_id === currentItem.binding_id,
    );
    const operationId = crypto.randomUUID();
    const operation: CandidateOutboxOperation = {
      attemptId,
      bindingId: currentItem.binding_id,
      operationId,
      expectedRevision: existing?.revision ?? 0,
      payload,
      queuedAt: new Date().toISOString(),
    };
    setResponse(payload);
    if (!online) {
      await queueCandidateOperation(operation);
      setStatus("Offline — queued");
      return;
    }
    setStatus("Saving");
    try {
      const saved = await candidateApi.save(
        attemptId,
        currentItem.binding_id,
        payload,
        operation.expectedRevision,
        operationId,
      );
      setRuntime((current) =>
        current
          ? {
              ...current,
              responses: current.responses.map((item) =>
                item.binding_id === currentItem.binding_id
                  ? {
                      ...item,
                      revision: saved.revision,
                      payload: saved.payload,
                    }
                  : item,
              ),
            }
          : current,
      );
      setStatus("Saved");
    } catch (cause) {
      setStatus(
        cause instanceof CandidateApiError && cause.status === 409
          ? "Conflict"
          : "Expired",
      );
      setError(safeError(cause));
    }
  }

  async function navigateTo(bindingId: string) {
    if (!attemptId || !editable) return;
    try {
      await candidateApi.navigate(attemptId, bindingId);
      await loadRuntime(attemptId, "runtime");
      setError(null);
      questionHeadingRef.current?.focus();
    } catch (cause) {
      setError(safeError(cause));
    }
  }

  async function submit() {
    if (!attemptId || !editable) return;
    try {
      const submitted = await candidateApi.submit(attemptId);
      setRuntime((current) =>
        current ? { ...current, status: submitted.status } : current,
      );
      setStatus("Submitted");
    } catch (cause) {
      setError(safeError(cause));
    }
  }

  if (screen === "loading")
    return (
      <main>
        <p role="status">Loading exam…</p>
      </main>
    );
  if (screen === "unavailable")
    return (
      <main>
        <h1>Exam unavailable</h1>
        <p role="alert">{error ?? "This exam is unavailable."}</p>
      </main>
    );
  if (screen === "prestart") {
    const duration = runtime?.runtime_plan.duration_seconds;
    return (
      <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
        <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
          Candidate exam
        </p>
        <h1>Exam assignment</h1>
        <p>
          This pre-start screen does not start the attempt or begin the timer.
        </p>
        <p>Availability: confirmed by the server.</p>
        <p>
          Duration:{" "}
          {duration ? `${Math.ceil(duration / 60)} minutes` : "Server-defined"}
        </p>
        <p>
          Content is delivered only after the server confirms the start
          operation.
        </p>
        <h2>Instructions</h2>
        <p>
          Answer each question using the supported response control. Your
          progress is saved by the server.
        </p>
        {error && <p role="alert">{error}</p>}
        {!attemptId ? (
          <button type="button" onClick={prepareAttempt} disabled={starting}>
            {starting ? "Preparing exam…" : "Prepare exam"}
          </button>
        ) : (
          <button type="button" onClick={startAttempt} disabled={starting}>
            {starting ? "Starting exam…" : "Start exam"}
          </button>
        )}
      </main>
    );
  }

  const content = (currentItem?.content ?? {}) as {
    prompt?: string;
    options?: Array<{ option_id: string; display_text?: string }>;
  };
  const mode = (currentItem?.response_mode ?? "LONG_TEXT") as ResponseMode;
  const selected = (response.option_ids as string[] | undefined) ?? [];
  const textValue = (response.text as string | undefined) ?? "";
  const mediaAssetId = response.media_asset_id as string | undefined;
  const message = runtimeStateMessage(status);
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Candidate exam
      </p>
      <h1>Runtime</h1>
      <p>Attempt {attemptId} · Server time controls the deadline.</p>
      <RuntimeTimer
        deadlineAt={
          runtime?.deadline_at ? Date.parse(runtime.deadline_at) : null
        }
      />
      <p>
        Module and task position: {currentIndex + 1} of {items.length}
      </p>
      <p>Connection: {online ? "Online" : "Offline — changes are queued"}</p>
      <p
        role={status === "Saved" || status === "Saving" ? "status" : "alert"}
        aria-live={status === "Saved" || status === "Saving" ? "off" : "polite"}
      >
        Autosave: {status}
      </p>
      {message && (
        <p id={errorId} role="alert">
          {message}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <section aria-labelledby="question-heading">
        <h2 id="question-heading" ref={questionHeadingRef} tabIndex={-1}>
          Question
        </h2>
        <p id={promptId}>{content.prompt ?? "Question content"}</p>
        <p id={responseHelpId}>Response mode: {modeLabels[mode] ?? mode}</p>
        {(mode === "SELECT_ONE" || mode === "SELECT_MULTIPLE") && (
          <fieldset aria-describedby={`${promptId} ${responseHelpId}`}>
            <legend>
              {mode === "SELECT_ONE"
                ? "Select one answer"
                : "Select all answers that apply"}
            </legend>
            {(content.options ?? []).map((option) => (
              <label key={option.option_id} style={{ display: "block" }}>
                <input
                  type={mode === "SELECT_ONE" ? "radio" : "checkbox"}
                  name="candidate-response"
                  checked={
                    mode === "SELECT_ONE"
                      ? response.option_id === option.option_id
                      : selected.includes(option.option_id)
                  }
                  onChange={() =>
                    void persist(
                      mode === "SELECT_ONE"
                        ? { option_id: option.option_id }
                        : {
                            option_ids: selected.includes(option.option_id)
                              ? selected.filter((id) => id !== option.option_id)
                              : [...selected, option.option_id],
                          },
                    )
                  }
                  disabled={!editable}
                />{" "}
                {option.display_text ?? option.option_id}
              </label>
            ))}
          </fieldset>
        )}
        {mode === "TEXT" && (
          <label htmlFor="candidate-text">
            Short response
            <input
              id="candidate-text"
              value={textValue}
              onChange={(event) => setResponse({ text: event.target.value })}
              onBlur={() => void persist(response)}
              aria-describedby={`${promptId} ${responseHelpId}`}
              disabled={!editable}
              maxLength={4000}
            />
          </label>
        )}
        {mode === "LONG_TEXT" && (
          <label htmlFor="candidate-long-text">
            Long response
            <textarea
              id="candidate-long-text"
              value={textValue}
              onChange={(event) => setResponse({ text: event.target.value })}
              onBlur={() => void persist(response)}
              aria-describedby={`${promptId} ${responseHelpId}`}
              disabled={!editable}
              rows={8}
              maxLength={20000}
            />
          </label>
        )}
        {mode === "AUDIO" && attemptId && currentItem && (
          <AudioResponseControl
            attemptId={attemptId}
            bindingId={currentItem.binding_id}
            editable={Boolean(editable)}
            currentMediaAssetId={mediaAssetId}
            onSaved={(assetId) => persist({ media_asset_id: assetId })}
          />
        )}
      </section>
      <nav aria-label="Exam question navigation">
        <p aria-current="step">Current question {currentIndex + 1}</p>
        <button
          type="button"
          onClick={() =>
            currentIndex > 0 &&
            void navigateTo(items[currentIndex - 1].binding_id)
          }
          disabled={!editable || currentIndex <= 0}
        >
          Previous question
        </button>
        <button
          type="button"
          onClick={() =>
            currentIndex >= 0 &&
            currentIndex < items.length - 1 &&
            void navigateTo(items[currentIndex + 1].binding_id)
          }
          disabled={
            !editable || currentIndex < 0 || currentIndex >= items.length - 1
          }
        >
          Next question
        </button>
      </nav>
      <button type="button" onClick={() => void submit()} disabled={!editable}>
        Submit attempt
      </button>
    </main>
  );
}
