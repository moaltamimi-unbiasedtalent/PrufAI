"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "../../../../lib/auth";
import { pilotActive } from "../../../../lib/access";
import type { LidPracticeAssignment } from "../../../../lib/lid-practice";
import { WriterPanel, type WriterPhase } from "./writer-panel";
import { LidPracticeResultPanel } from "./lid-practice-result";
import {
  CandidateApiError,
  candidateApi,
  isDefiniteSubmissionRejection,
  type RuntimeState as RuntimeDto,
  type CandidateWriter,
} from "../../api";
import {
  AudioResponseControl,
  applySavedResponse,
  RuntimeTimer,
  runtimeStateMessage,
  type ResponseMode,
  type RuntimeState,
} from "./runtime-ux";
import {
  CandidateOutboxBlocked,
  candidateWriterIdentity,
  releaseSupersededCandidateSubmission,
  discardCandidateOperations,
  fenceCandidateSubmission,
  notifyCandidateOutbox,
  observeCandidateOutbox,
  queueCandidateOperation,
  readCandidateAttemptBarrier,
  releaseRejectedCandidateSubmission,
  syncCandidateOutbox,
  withCandidateAttemptLock,
  type CandidateAccountScope,
  type CandidateOutboxOperation,
} from "../../outbox";
import type { AudioAttachmentAck } from "../../audio-contract";
import styles from "./candidate-practice.module.css";

const RECOVERY_TIMEOUT_MS = 8_000;

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

const practiceModeLabels: Record<ResponseMode, string> = {
  SELECT_ONE: "Einfachauswahl",
  SELECT_MULTIPLE: "Mehrfachauswahl",
  TEXT: "Kurzantwort",
  LONG_TEXT: "Freitext",
  AUDIO: "Audioaufnahme",
};

function flattenItems(runtime: RuntimeDto): RuntimeItem[] {
  return (runtime.runtime_plan.modules ?? []).flatMap((module) =>
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

function safeError(cause: unknown, german = false): string {
  if (german) {
    if (cause instanceof CandidateApiError) {
      if (cause.code === "PILOT_APPROVED_MANIFEST_REQUIRED")
        return "Diese Übung ist noch nicht zur Teilnahme freigegeben.";
      if (cause.code === "PILOT_SCOPE_DENIED")
        return "Dieses Angebot ist im Berliner Übungsbereich nicht verfügbar.";
      if (cause.status === 401)
        return "Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.";
      if (cause.code === "CONTENT_UNAVAILABLE")
        return "Für dieses Bundesland sind keine Übungsfragen verfügbar.";
      if (cause.status === 403 || cause.status === 404)
        return "Diese Übung ist für Ihr Konto nicht verfügbar. Wenden Sie sich bei Fragen an Ihre Institution.";
      if (cause.status === 409)
        return "Der Versuch hat sich geändert. Laden Sie die Seite neu und prüfen Sie den Status.";
    }
    return "Die Übung ist vorübergehend nicht erreichbar. Prüfen Sie die Verbindung und versuchen Sie es erneut.";
  }
  if (!(cause instanceof CandidateApiError))
    return "The runtime is temporarily unavailable.";
  if (cause.code === "CONTENT_UNAVAILABLE")
    return "Practice questions are unavailable for this Bundesland.";
  if (cause.code.includes("UNSUPPORTED_RESPONSE_MODE"))
    return "This exam contains a response type that is not available in Candidate Runtime.";
  if (cause.status === 401) return "Bitte melden Sie sich erneut an.";
  if (
    cause.status === 403 ||
    cause.status === 404 ||
    cause.code === "candidate attempt not found"
  )
    return "This exam attempt is unavailable to the authenticated candidate.";
  if (cause.code.includes("available") || cause.code.includes("limit"))
    return "This assignment cannot be started right now.";
  if (cause.status === 409)
    return "The server rejected this action because the attempt state changed. Refresh and try again.";
  return "The runtime is temporarily unavailable.";
}

function isExpired(cause: unknown): boolean {
  return (
    cause instanceof CandidateApiError &&
    cause.code === "RUNTIME.ATTEMPT_EXPIRED"
  );
}

export default function CandidateExamRuntimePage() {
  const router = useRouter();
  const { user, organisationId } = useAuth();
  const userId = user?.id;
  const accountScope = useMemo<CandidateAccountScope | null>(
    () => (userId && organisationId ? { userId, organisationId } : null),
    [userId, organisationId],
  );
  const params = useParams<{ attemptId: string }>();
  // The localized link only selects UI copy before the authenticated response.
  const germanRoute = params.attemptId.startsWith("de-");
  const routeId = germanRoute ? params.attemptId.slice(3) : params.attemptId;
  const contextKey =
    userId && organisationId ? `${userId}:${organisationId}:${routeId}` : null;
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  const [runtimeContext, setRuntimeContext] = useState<string | null>(null);
  const assignmentId = routeId.startsWith("assignment-")
    ? routeId.slice("assignment-".length)
    : null;
  const promptId = useId();
  const responseHelpId = `${promptId}-help`;
  const errorId = `${promptId}-error`;
  const [screen, setScreen] = useState<Screen>("loading");
  const [lidAssignmentInfo, setLidAssignmentInfo] = useState<{
    context: string | null;
    assignment: LidPracticeAssignment;
  } | null>(null);
  const [practiceState, setPracticeState] = useState("");
  const [attemptId, setAttemptId] = useState<string | null>(
    assignmentId ? null : routeId,
  );
  const [runtime, setRuntime] = useState<RuntimeDto | null>(null);
  const [status, setStatus] = useState<RuntimeState>("Saved");
  const [online, setOnline] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadRetry, setLoadRetry] = useState(0);
  const [response, setResponse] = useState<Record<string, unknown>>({});
  const [starting, setStarting] = useState(false);
  const [pendingSave, setPendingSave] = useState(false);
  const [resolvedPracticeOutbox, setResolvedPracticeOutbox] = useState<
    string | null
  >(null);
  const [practiceOutbox, setPracticeOutbox] = useState<{
    context: string | null;
    attemptId: string;
    bindingIds: ReadonlySet<string>;
  } | null>(null);
  const [sharedSubmissionStarted, setSharedSubmissionStarted] = useState(false);
  const [audioPending, setAudioPending] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submissionUnconfirmed, setSubmissionUnconfirmed] = useState(false);
  const [retryingSave, setRetryingSave] = useState(false);
  const [retrySafety, setRetrySafety] = useState(false);
  const submissionHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusQuestion = useRef(false);
  const focusSubmission = useRef(false);
  const submittingRef = useRef(false);
  const submitDialogRef = useRef<HTMLDialogElement>(null);
  const submitTriggerRef = useRef<HTMLButtonElement>(null);
  const cancelSubmitRef = useRef<HTMLButtonElement>(null);
  const submitTitleId = useId();
  const submitDescriptionId = useId();
  const navigatingRef = useRef(false);
  useEffect(() => {
    navigatingRef.current = false;
    setNavigating(false);
  }, [contextKey]);
  const audioPendingRef = useRef(false);
  const audioActivity = useCallback((active: boolean) => {
    audioPendingRef.current = active;
    setAudioPending(active);
  }, []);
  const questionHeadingRef = useRef<HTMLHeadingElement>(null);
  const runtimeRef = useRef<RuntimeDto | null>(null);
  const writerRef = useRef<{
    context: string | null;
    attemptId: string;
    credential: CandidateWriter;
  } | null>(null);
  const [writerState, setWriterState] = useState<{
    context: string | null;
    phase: WriterPhase;
  }>({ context: null, phase: "checking" });
  const claimingWriter = useRef(false);
  const claimingWriterToken = useRef(0);
  const writerAuthoritySequence = useRef(0);
  const lastWriterRefusal = useRef<{
    context: string | null;
    generation: number;
  } | null>(null);
  const requireWriter = useCallback((): CandidateWriter | undefined => {
    if (!runtimeRef.current?.runtime_plan.lid_practice) return undefined;
    const writer = writerRef.current;
    if (
      !writer ||
      writer.context !== currentContext.current ||
      writer.attemptId !== runtimeRef.current.attempt_id ||
      writer.credential.generation !== runtimeRef.current.writer?.generation
    )
      throw new CandidateOutboxBlocked("writer authority unavailable");
    return writer.credential;
  }, []);
  const saveSequence = useRef<Promise<void>>(Promise.resolve());
  const saveBlocked = useRef(false);
  const savingPracticeResponse = useRef(false);
  const retryingPracticeResponse = useRef(false);
  const rejectedPracticeOperations = useRef(new Set<string>());

  const updateRuntime = useCallback(
    (update: (current: RuntimeDto | null) => RuntimeDto | null) => {
      const state = update(runtimeRef.current);
      if (
        state?.runtime_plan.lid_practice &&
        writerRef.current &&
        state.writer?.generation !== writerRef.current.credential.generation
      ) {
        writerRef.current = null;
        setWriterState({ context: currentContext.current, phase: "held" });
      }
      runtimeRef.current = state;
      setRuntime(state);
    },
    [],
  );

  const writerFailure = useCallback(
    (cause: unknown) => {
      if (
        !(cause instanceof CandidateApiError) ||
        !cause.code.startsWith("RUNTIME.WRITER_")
      )
        return false;
      const authoritySequence = ++writerAuthoritySequence.current;
      const expected = currentContext.current,
        previous = writerRef.current,
        id = runtimeRef.current?.attempt_id;
      writerRef.current = null;
      setWriterState({ context: expected, phase: "checking" });
      setError(
        "Die Bearbeitungsfreigabe hat sich geändert. Ihre unbestätigten Antworten bleiben auf diesem Gerät erhalten. Prüfen Sie den gespeicherten Stand vor einer Übernahme.",
      );
      if (id)
        void candidateApi
          .writerStatus(
            id,
            previous?.credential.clientId,
            AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
          )
          .then((state) => {
            if (
              currentContext.current !== expected ||
              authoritySequence !== writerAuthoritySequence.current
            )
              return;
            lastWriterRefusal.current = state.owned
              ? null
              : { context: expected, generation: state.generation };
            updateRuntime((current) =>
              current?.attempt_id === id
                ? { ...current, status: state.status, writer: state }
                : current,
            );
            setWriterState({
              context: expected,
              phase: state.owned ? "checking" : "held",
            });
          })
          .catch(() => {
            if (
              currentContext.current === expected &&
              authoritySequence === writerAuthoritySequence.current
            )
              setWriterState({ context: expected, phase: "unavailable" });
          });
      return true;
    },
    [updateRuntime],
  );

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
  const lidForm =
    runtimeContext === contextKey ? runtime?.runtime_plan.lid_practice : null;
  const isLidPractice = Boolean(lidForm);
  const writerRequired =
    isLidPractice &&
    (runtime?.status === "READY" || runtime?.status === "IN_PROGRESS");
  const writerOwned =
    writerState.context === contextKey &&
    writerState.phase === "owned" &&
    writerRef.current?.context === contextKey &&
    writerRef.current?.credential.generation === runtime?.writer?.generation;
  const writerPhase: WriterPhase =
    writerState.context === contextKey ? writerState.phase : "checking";

  const runtimeReady =
    runtimeContext === contextKey && screen === "runtime" && editable;
  // Render the LiD attempt as pending before the effect can inspect IndexedDB.
  const practiceReplayPending =
    isLidPractice &&
    runtimeReady &&
    (resolvedPracticeOutbox !== contextKey || !writerOwned);
  const unconfirmedBindings =
    practiceOutbox?.context === contextKey &&
    practiceOutbox.attemptId === attemptId
      ? practiceOutbox.bindingIds
      : null;
  const lidAssignment =
    lidAssignmentInfo?.context === contextKey
      ? lidAssignmentInfo?.assignment
      : null;
  const initialGerman = germanRoute || pilotActive(user);
  const germanPractice = initialGerman || Boolean(lidForm || lidAssignment);
  const selectedPracticeState = lidAssignment?.bundeslaender.find(
    (state) => state.code === practiceState && state.available,
  );
  const savedRevision =
    runtime?.responses.find((item) => item.binding_id === currentBinding)
      ?.revision ?? 0;

  const markExpired = useCallback(() => {
    const current = runtimeRef.current;
    if (!current || current.status === "SUBMITTED") return;
    const binding =
      current.session.current_binding_id ??
      flattenItems(current)[0]?.binding_id;
    setResponse(
      current.responses.find((answer) => answer.binding_id === binding)
        ?.payload ?? {},
    );
    updateRuntime((state) => state && { ...state, status: "EXPIRED" });
    setStatus("Expired");
    setError(null);
    submitDialogRef.current?.close();
  }, [updateRuntime]);

  const loadRuntime = useCallback(
    async (id: string, nextScreen?: Screen) => {
      const expectedContext = currentContext.current;
      const state = await candidateApi.runtime(
        id,
        AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
      );
      if (!expectedContext || currentContext.current !== expectedContext)
        return;
      setRuntimeContext(expectedContext);
      setAttemptId(id);
      updateRuntime(() => state);
      const bindingId =
        state.session.current_binding_id ?? flattenItems(state)[0]?.binding_id;
      const persisted = state.responses.find(
        (item) => item.binding_id === bindingId,
      );
      setResponse(persisted?.payload ?? {});
      if (state.status === "EXPIRED") setStatus("Expired");
      if (state.status === "SUBMITTED") setStatus("Submitted");
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
      return state;
    },
    [updateRuntime],
  );

  useEffect(() => {
    let active = true;
    if (!accountScope) return;
    if (assignmentId) {
      candidateApi
        .assignments()
        .then((rows) => {
          if (!active) return;
          const assignment = rows.find((row) => row.id === assignmentId);
          if (!assignment)
            throw new CandidateApiError(404, "assignment unavailable");
          setPracticeState("");
          setLidAssignmentInfo(
            assignment.lid_practice
              ? {
                  context: currentContext.current,
                  assignment: assignment.lid_practice,
                }
              : null,
          );
          setScreen("prestart");
        })
        .catch((cause: unknown) => {
          if (active) {
            setError(safeError(cause, initialGerman));
            setScreen("unavailable");
          }
        });
    } else {
      loadRuntime(routeId).catch((cause: unknown) => {
        if (active) {
          setError(safeError(cause, initialGerman));
          setScreen("unavailable");
        }
      });
    }
    return () => {
      active = false;
    };
  }, [
    assignmentId,
    routeId,
    accountScope,
    loadRuntime,
    loadRetry,
    initialGerman,
  ]);

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

  const refreshAttempt = useCallback(() => {
    if (!attemptId || !editable) return;
    const expectedContext = currentContext.current;
    void candidateApi
      .heartbeat(attemptId)
      .then((state) => {
        if (
          currentContext.current !== expectedContext ||
          runtimeRef.current?.status !== "IN_PROGRESS"
        )
          return;
        if (state.status === "EXPIRED") {
          markExpired();
          if (isLidPractice) void loadRuntime(attemptId).catch(() => undefined);
        } else {
          if (state.status === "SUBMITTED") {
            setStatus("Submitted");
            setSubmissionUnconfirmed(false);
            setError(null);
            submitDialogRef.current?.close();
          }
          updateRuntime(
            (current) =>
              current && {
                ...current,
                status: state.status,
                deadline_at: state.deadline_at,
                monotonic_deadline: state.monotonic_deadline,
                writer: state.writer ?? current.writer,
              },
          );
        }
      })
      .catch(() => undefined);
  }, [
    attemptId,
    editable,
    isLidPractice,
    loadRuntime,
    markExpired,
    updateRuntime,
  ]);

  useEffect(() => {
    if (!editable) return;
    const whenVisible = () => {
      if (document.visibilityState === "visible") refreshAttempt();
    };
    const heartbeat = window.setInterval(refreshAttempt, 30000);
    document.addEventListener("visibilitychange", whenVisible);
    return () => {
      window.clearInterval(heartbeat);
      document.removeEventListener("visibilitychange", whenVisible);
    };
  }, [editable, refreshAttempt]);

  useEffect(() => {
    if (focusQuestion.current && screen === "runtime" && editable) {
      focusQuestion.current = false;
      questionHeadingRef.current?.focus();
    }
  }, [editable, screen, currentBinding]);

  useEffect(() => {
    if (focusSubmission.current && runtime?.status === "SUBMITTED") {
      focusSubmission.current = false;
      submissionHeadingRef.current?.focus();
    }
  }, [runtime?.status]);

  useEffect(() => {
    writerRef.current = null;
    claimingWriter.current = false;
    claimingWriterToken.current = 0;
    writerAuthoritySequence.current += 1;
    lastWriterRefusal.current = null;
    setWriterState({ context: contextKey, phase: "checking" });
    rejectedPracticeOperations.current.clear();
    setResolvedPracticeOutbox(null);
    setPracticeOutbox(null);
    setSharedSubmissionStarted(false);
    savingPracticeResponse.current = false;
    retryingPracticeResponse.current = false;
    saveBlocked.current = false;
    setRetrySafety(false);
    setRetryingSave(false);
    setSubmissionUnconfirmed(false);
  }, [contextKey]);

  const refreshPracticeBindings = useCallback(
    async (scope: CandidateAccountScope, id: string, active: () => boolean) => {
      const barrier = await readCandidateAttemptBarrier(scope, id);
      if (active()) {
        setPracticeOutbox({
          context: contextKey,
          attemptId: id,
          bindingIds: barrier.bindingIds,
        });
        setSharedSubmissionStarted(barrier.submissionStarted);
      }
      return barrier;
    },
    [contextKey],
  );

  const acquireWriter = useCallback(
    async (
      acknowledge = false,
      expectedGeneration?: number,
    ): Promise<boolean> => {
      if (!accountScope || !attemptId || claimingWriter.current) return false;
      if (!online && writerRef.current?.context === contextKey) return true;
      const authoritySequence = ++writerAuthoritySequence.current;
      const active = () =>
        currentContext.current === contextKey &&
        authoritySequence === writerAuthoritySequence.current;
      claimingWriter.current = true;
      claimingWriterToken.current = authoritySequence;
      setWriterState({ context: contextKey, phase: "checking" });
      try {
        await withCandidateAttemptLock(accountScope, attemptId, async () => {
          const identity = await candidateWriterIdentity(
            accountScope,
            attemptId,
          );
          if (!active())
            throw new CandidateOutboxBlocked("candidate context changed");
          const claimed = await candidateApi.claimWriter(
            attemptId,
            identity,
            expectedGeneration ?? runtimeRef.current?.writer?.generation ?? 0,
            acknowledge,
            AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
          );
          if (!active()) return;
          const state = await loadRuntime(attemptId);
          if (
            !active() ||
            !state ||
            state.writer?.generation !== claimed.generation ||
            !["READY", "IN_PROGRESS"].includes(state.status)
          )
            throw new CandidateOutboxBlocked(
              "writer state could not be verified",
            );
          if (state.status === "IN_PROGRESS") {
            if (
              await releaseSupersededCandidateSubmission(
                accountScope,
                attemptId,
                claimed.generation,
              )
            ) {
              setSubmissionUnconfirmed(false);
              setSharedSubmissionStarted(false);
              saveBlocked.current = false;
            }
            await refreshPracticeBindings(accountScope, attemptId, active);
          }
          if (!active()) return;
          lastWriterRefusal.current = null;
          writerRef.current = {
            context: contextKey,
            attemptId,
            credential: { clientId: identity, generation: claimed.generation },
          };
          setWriterState({ context: contextKey, phase: "owned" });
          setError(null);
          requestAnimationFrame(() => questionHeadingRef.current?.focus());
        });
        return active() && writerRef.current?.context === contextKey;
      } catch (cause) {
        if (!active()) return false;
        writerRef.current = null;
        try {
          await loadRuntime(attemptId);
          await refreshPracticeBindings(accountScope, attemptId, active);
        } catch {
          /* Remain read-only when verification is unavailable. */
        }
        if (
          active() &&
          cause instanceof CandidateApiError &&
          cause.code.startsWith("RUNTIME.WRITER_")
        )
          lastWriterRefusal.current = {
            context: contextKey,
            generation: runtimeRef.current?.writer?.generation ?? 0,
          };
        if (active()) setResolvedPracticeOutbox(contextKey);
        if (active())
          setWriterState({
            context: contextKey,
            phase:
              cause instanceof CandidateApiError &&
              cause.code.startsWith("RUNTIME.WRITER_")
                ? "held"
                : "unavailable",
          });
        return false;
      } finally {
        if (claimingWriterToken.current === authoritySequence) {
          claimingWriter.current = false;
          claimingWriterToken.current = 0;
        }
      }
    },
    [
      accountScope,
      attemptId,
      online,
      contextKey,
      loadRuntime,
      refreshPracticeBindings,
    ],
  );

  useEffect(() => {
    // Preparing an assignment replaces the route. Do not acquire under the old
    // assignment context while that replacement is still in progress.
    if (runtimeContext !== contextKey || routeId !== attemptId) return;
    if (
      lastWriterRefusal.current?.context === contextKey &&
      lastWriterRefusal.current.generation === runtime?.writer?.generation
    )
      return;
    if (
      writerRef.current?.context === contextKey &&
      writerRef.current.credential.generation === runtime?.writer?.generation
    )
      return;
    if (
      isLidPractice &&
      ["READY", "IN_PROGRESS"].includes(runtime?.status ?? "")
    )
      void acquireWriter();
  }, [
    isLidPractice,
    runtimeContext,
    routeId,
    attemptId,
    runtime?.status,
    runtime?.writer?.generation,
    contextKey,
    acquireWriter,
  ]);

  useEffect(() => {
    if (
      !isLidPractice ||
      !runtimeReady ||
      !accountScope ||
      !attemptId ||
      !navigator.locks
    )
      return;
    const id = attemptId;
    const scope = accountScope;
    let active = true;
    let observation = 0;
    let needsRuntime = false;
    let hadUnresolved = false;
    let refreshing = false;
    const localChange = () =>
      claimingWriter.current ||
      savingPracticeResponse.current ||
      retryingPracticeResponse.current ||
      submittingRef.current ||
      navigatingRef.current;
    const refresh = async (notified = false) => {
      if (notified) needsRuntime = true;
      if (
        localChange() ||
        resolvedPracticeOutbox !== contextKey ||
        (refreshing && !notified)
      )
        return;
      const serial = ++observation;
      const authoritySequence = writerAuthoritySequence.current;
      refreshing = true;
      const current = () =>
        active &&
        serial === observation &&
        authoritySequence === writerAuthoritySequence.current &&
        currentContext.current === contextKey &&
        !localChange();
      if (notified) setPracticeOutbox(null);
      try {
        const barrier = await readCandidateAttemptBarrier(scope, id);
        if (!current()) return;
        const { bindingIds, submissionStarted } = barrier;
        // Offline observation still uses the authoritative shared local queue.
        // A network probe cannot replace known pending answers with uncertainty.
        if (!navigator.onLine) {
          hadUnresolved = Boolean(bindingIds.size);
          setSharedSubmissionStarted(submissionStarted);
          setPracticeOutbox({ context: contextKey, attemptId: id, bindingIds });
          return;
        }
        const marker = await candidateWriterIdentity(scope, id);
        const authority = await candidateApi.writerStatus(
          id,
          marker,
          AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
        );
        if (!current()) return;
        if (
          !authority.owned &&
          ["READY", "IN_PROGRESS"].includes(authority.status)
        ) {
          writerRef.current = null;
          lastWriterRefusal.current = {
            context: contextKey,
            generation: authority.generation,
          };
          setWriterState({ context: contextKey, phase: "held" });
        } else if (
          authority.owned &&
          (!writerRef.current ||
            writerRef.current.credential.generation !== authority.generation)
        ) {
          lastWriterRefusal.current = null;
          writerRef.current = null;
          void acquireWriter();
        }
        updateRuntime((current) =>
          current?.attempt_id === id
            ? { ...current, writer: authority, status: authority.status }
            : current,
        );
        if (
          !bindingIds.size &&
          (needsRuntime || hadUnresolved || submissionStarted)
        ) {
          const state = await candidateApi.runtime(
            id,
            AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
          );
          // The outbox may have changed while the runtime request was pending.
          const latest = await readCandidateAttemptBarrier(scope, id);
          if (!current()) return;
          if (
            latest.bindingIds.size ||
            latest.submissionStarted !== submissionStarted
          ) {
            setPracticeOutbox(null);
            return;
          }
          updateRuntime(
            (previous) =>
              previous && {
                ...previous,
                status: state.status,
                responses: state.responses,
              },
          );
          const shownBinding =
            runtimeRef.current?.session.current_binding_id ??
            flattenItems(state)[0]?.binding_id;
          setResponse(
            state.responses.find((answer) => answer.binding_id === shownBinding)
              ?.payload ?? {},
          );
          saveBlocked.current = false;
          rejectedPracticeOperations.current.clear();
          setRetrySafety(false);
          setStatus(
            state.status === "SUBMITTED"
              ? "Submitted"
              : state.status === "EXPIRED"
                ? "Expired"
                : "Saved",
          );
          needsRuntime = false;
        }
        hadUnresolved = Boolean(bindingIds.size);
        setSharedSubmissionStarted(submissionStarted);
        setPracticeOutbox({ context: contextKey, attemptId: id, bindingIds });
      } catch {
        if (current()) {
          needsRuntime = true;
          setPracticeOutbox(null);
        }
      } finally {
        if (serial === observation) refreshing = false;
      }
    };
    const stop = observeCandidateOutbox(scope, id, () => void refresh(true));
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    const poll = window.setInterval(onFocus, 2000);
    return () => {
      active = false;
      stop();
      window.removeEventListener("focus", onFocus);
      window.clearInterval(poll);
    };
  }, [
    accountScope,
    attemptId,
    contextKey,
    isLidPractice,
    resolvedPracticeOutbox,
    runtimeReady,
    updateRuntime,
    acquireWriter,
  ]);

  const drainPractice = useCallback(
    async (
      scope: CandidateAccountScope,
      id: string,
      active: () => boolean,
      reconcile: boolean,
    ) => {
      const before = await refreshPracticeBindings(scope, id, active);
      if (!active()) return;
      if (before.submissionStarted) {
        await loadRuntime(id);
        return;
      }
      let expired = false;
      const result = await syncCandidateOutbox(
        scope,
        async (operation) => {
          if (!active()) throw new Error("candidate context changed");
          try {
            const saved = await candidateApi.save(
              operation.attemptId,
              operation.bindingId,
              operation.payload,
              operation.expectedRevision,
              operation.operationId,
              AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
              requireWriter(),
            );
            if (!active()) throw new Error("candidate context changed");
            updateRuntime((current) =>
              current?.attempt_id === id
                ? applySavedResponse(current, operation.bindingId, saved)
                : current,
            );
            rejectedPracticeOperations.current.delete(operation.operationId);
            return "ack";
          } catch (cause) {
            if (!active()) throw cause;
            if (writerFailure(cause)) return "retry";
            if (isExpired(cause)) {
              expired = true;
              markExpired();
              return "expired";
            }
            if (
              cause instanceof CandidateApiError &&
              cause.status === 409 &&
              (cause.code === "stale response revision" ||
                cause.code === "RUNTIME.IDEMPOTENCY_KEY_REUSED")
            ) {
              rejectedPracticeOperations.current.add(operation.operationId);
              return "conflict";
            }
            return "retry";
          }
        },
        id,
        { retainUnresolved: true },
      );
      if (!active()) return;
      if (expired) {
        await loadRuntime(id).catch(() => undefined);
        return;
      }
      const remaining = await refreshPracticeBindings(scope, id, active);
      if (!active()) return;
      saveBlocked.current = Boolean(
        result.retryNeeded || result.conflicts || remaining.bindingIds.size,
      );
      if (saveBlocked.current) {
        setRetrySafety(true);
        setStatus(result.conflicts ? "Conflict" : "Save failed");
        return;
      }
      if (reconcile) {
        saveBlocked.current = true;
        const state = await loadRuntime(id);
        if (!active()) return;
        if (state?.status !== "IN_PROGRESS") {
          saveBlocked.current = false;
          setRetrySafety(false);
          return;
        }
      }
      saveBlocked.current = false;
      setRetrySafety(false);
      setError(null);
      setStatus("Saved");
    },
    [
      loadRuntime,
      markExpired,
      refreshPracticeBindings,
      updateRuntime,
      requireWriter,
      writerFailure,
    ],
  );

  function retryPractice(discardConflict = false) {
    if (
      !online ||
      !attemptId ||
      !accountScope ||
      pendingSave ||
      retryingPracticeResponse.current ||
      !editable ||
      (isLidPractice && !writerOwned)
    )
      return;
    const active = () => currentContext.current === contextKey;
    const id = attemptId;
    retryingPracticeResponse.current = true;
    setPendingSave(true);
    setRetryingSave(true);
    setStatus("Saving");
    const task = withCandidateAttemptLock(accountScope, id, async () => {
      if (!active()) return;
      if (discardConflict) {
        const rejected = new Set(rejectedPracticeOperations.current);
        const state = await loadRuntime(id);
        if (!active() || state?.status !== "IN_PROGRESS") return;
        await discardCandidateOperations(accountScope, id, rejected);
        if (!active()) return;
        rejectedPracticeOperations.current.clear();
      }
      await drainPractice(accountScope, id, active, true);
      if (active() && !saveBlocked.current) questionHeadingRef.current?.focus();
    })
      .catch(() => {
        if (!active()) return;
        saveBlocked.current = true;
        setRetrySafety(true);
        setStatus(discardConflict ? "Conflict" : "Save failed");
      })
      .finally(() => {
        notifyCandidateOutbox(accountScope, id);
        if (active()) {
          retryingPracticeResponse.current = false;
          setPendingSave(false);
          setRetryingSave(false);
        }
      });
    saveSequence.current = task;
  }

  useEffect(() => {
    if (
      !attemptId ||
      !accountScope ||
      !runtimeReady ||
      (isLidPractice && !writerOwned)
    )
      return;
    if (!online) {
      if (!savingPracticeResponse.current) setPendingSave(false);
      return;
    }
    let active = true;
    let expired = false;
    setPendingSave(true);
    const task = saveSequence.current.then(async () => {
      if (!active) return;
      if (isLidPractice) {
        await withCandidateAttemptLock(accountScope, attemptId, () =>
          drainPractice(accountScope, attemptId, () => active, true),
        );
        return;
      }
      return syncCandidateOutbox(
        accountScope,
        async (operation: CandidateOutboxOperation) => {
          if (!active) throw new Error("candidate account changed");
          try {
            const saved = await candidateApi.save(
              operation.attemptId,
              operation.bindingId,
              operation.payload,
              operation.expectedRevision,
              operation.operationId,
            );
            if (!active) throw new Error("candidate account changed");
            updateRuntime((current) =>
              current?.attempt_id === operation.attemptId
                ? applySavedResponse(current, operation.bindingId, saved)
                : current,
            );
            return "ack";
          } catch (cause) {
            if (active && isExpired(cause)) {
              expired = true;
              saveBlocked.current = true;
              markExpired();
              return "expired";
            }
            if (cause instanceof CandidateApiError && cause.status === 409) {
              saveBlocked.current = true;
              setStatus("Conflict");
              return "conflict";
            }
            saveBlocked.current = true;
            return "conflict";
          }
        },
        attemptId,
      );
    });
    const complete = task
      .then((result) => {
        if (
          active &&
          result &&
          !expired &&
          result.acknowledged > 0 &&
          result.conflicts === 0
        ) {
          saveBlocked.current = false;
          setStatus("Saved");
        }
      })
      .catch(() => {
        if (active) {
          saveBlocked.current = true;
          if (isLidPractice) {
            setRetrySafety(true);
            setStatus("Save failed");
          } else {
            setError(
              "Queued responses could not be synchronized. They remain saved for this account.",
            );
          }
        }
      })
      .finally(() => {
        if (!active) return;
        if (isLidPractice) setResolvedPracticeOutbox(contextKey);
        setPendingSave(false);
        if (isLidPractice) notifyCandidateOutbox(accountScope, attemptId);
      });
    saveSequence.current = complete;
    return () => {
      active = false;
    };
  }, [
    online,
    attemptId,
    accountScope,
    isLidPractice,
    runtimeReady,
    writerOwned,
    contextKey,
    drainPractice,
    markExpired,
    updateRuntime,
  ]);

  async function prepareAttempt() {
    if (!assignmentId || (lidAssignment && !selectedPracticeState)) return;
    setStarting(true);
    setError(null);
    try {
      const attempt = await candidateApi.createAttempt(
        assignmentId,
        selectedPracticeState?.code,
      );
      await loadRuntime(attempt.id, "prestart");
      router.replace(
        `/candidate/exams/${lidAssignment ? "de-" : ""}${attempt.id}`,
      );
    } catch (cause) {
      setError(safeError(cause, Boolean(lidAssignment || lidForm)));
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
      const started = await candidateApi.start(attemptId, requireWriter());
      focusQuestion.current = Boolean(lidForm);
      updateRuntime(() => started);
      setScreen("runtime");
    } catch (cause) {
      setError(safeError(cause, Boolean(lidForm)));
    } finally {
      setStarting(false);
    }
  }

  async function persist(
    payload: Record<string, unknown>,
    audioOperationId?: string,
  ): Promise<AudioAttachmentAck | void> {
    if (
      !attemptId ||
      !currentItem ||
      !editable ||
      !accountScope ||
      runtimeContext !== contextKey
    ) {
      if (audioOperationId)
        throw new Error(
          "This response is no longer writable. Refresh the attempt.",
        );
      return;
    }
    if (
      lidForm &&
      (practiceReplayPending ||
        !unconfirmedBindings ||
        unconfirmedBindings.size > 0 ||
        sharedSubmissionStarted ||
        pendingSave ||
        saveBlocked.current ||
        savingPracticeResponse.current ||
        submittingRef.current ||
        submissionUnconfirmed)
    )
      return;
    if (lidForm) savingPracticeResponse.current = true;
    const expectedContext = contextKey;
    const bindingId = currentItem.binding_id;
    if (lidForm && !audioOperationId) {
      setPracticeOutbox({
        context: expectedContext,
        attemptId,
        bindingIds: new Set([...(unconfirmedBindings ?? []), bindingId]),
      });
    }
    const operationId = audioOperationId ?? crypto.randomUUID();
    let acknowledgement: AudioAttachmentAck | null = null;
    let failure: unknown;
    setResponse(payload);
    setPendingSave(true);
    const sendResponse = async () => {
      if (currentContext.current !== expectedContext)
        throw new Error("The candidate account or task changed.");
      const existing = runtimeRef.current?.responses.find(
        (item) => item.binding_id === bindingId,
      );
      const operation: CandidateOutboxOperation = {
        attemptId,
        bindingId,
        operationId,
        expectedRevision: existing?.revision ?? 0,
        payload,
        queuedAt: new Date().toISOString(),
      };
      if (lidForm && !audioOperationId) {
        requireWriter();
        const barrier = await readCandidateAttemptBarrier(
          accountScope,
          attemptId,
        );
        if (
          barrier.submissionStarted ||
          barrier.bindingIds.size ||
          currentContext.current !== expectedContext
        )
          throw new CandidateOutboxBlocked("attempt has an unresolved action");
        // Persist the operation before contacting the server so a reload can
        // replay the same ID and expected revision if acknowledgement is lost.
        await queueCandidateOperation(accountScope, operation, {
          exactRevision: true,
        });
        notifyCandidateOutbox(accountScope, attemptId);
        if (currentContext.current !== expectedContext) return;
        saveBlocked.current = true;
        setRetrySafety(true);
        if (!online) {
          setStatus("Offline — queued");
          return;
        }
        await drainPractice(
          accountScope,
          attemptId,
          () => currentContext.current === expectedContext,
          false,
        );
        return;
      }
      if (!online) {
        await queueCandidateOperation(accountScope, operation);
        saveBlocked.current = true;
        setStatus("Offline — queued");
        acknowledgement = { status: "QUEUED_RESPONSE" };
        return;
      }
      setStatus("Saving");
      try {
        const saved = await candidateApi.save(
          attemptId,
          bindingId,
          payload,
          operation.expectedRevision,
          operationId,
        );
        if (currentContext.current !== expectedContext)
          throw new Error("The candidate account or task changed.");
        if (
          audioOperationId &&
          (saved.payload.media_asset_id !== payload.media_asset_id ||
            !Number.isInteger(saved.revision) ||
            saved.revision < 1)
        )
          throw new Error(
            "The server did not confirm the exact audio response binding.",
          );
        updateRuntime((current) =>
          current ? applySavedResponse(current, bindingId, saved) : current,
        );
        saveBlocked.current = false;
        setError(null);
        setStatus("Saved");
        if (audioOperationId)
          acknowledgement = {
            status: "ATTACHED",
            media_asset_id: String(payload.media_asset_id),
            revision: saved.revision,
          };
      } catch (cause) {
        failure = cause;
        if (currentContext.current !== expectedContext) return;
        saveBlocked.current = true;
        if (isExpired(cause)) {
          markExpired();
          return;
        }
        setStatus(
          cause instanceof CandidateApiError && cause.status === 409
            ? "Conflict"
            : "Save failed",
        );
        setError(safeError(cause));
      }
    };
    const task = (
      lidForm
        ? withCandidateAttemptLock(accountScope, attemptId, sendResponse)
        : saveSequence.current.then(sendResponse)
    )
      .catch((cause: unknown) => {
        failure = cause;
        if (currentContext.current !== expectedContext) return;
        saveBlocked.current = true;
        setStatus("Save failed");
        if (lidForm) setRetrySafety(true);
        setError(
          lidForm
            ? "Die Antwort konnte auf diesem Gerät nicht gesichert werden. Laden Sie die bestätigte Antwort vom Server und wählen Sie erneut."
            : "Your response could not be saved locally. Keep this page open and reconnect before submitting.",
        );
      })
      .finally(() => {
        if (currentContext.current === expectedContext) {
          savingPracticeResponse.current = false;
          setPendingSave(false);
        }
        if (lidForm) notifyCandidateOutbox(accountScope, attemptId);
      });
    saveSequence.current = task;
    await task;
    if (audioOperationId) {
      if (failure) throw failure;
      if (acknowledgement) return acknowledgement;
      throw new Error(
        "Audio attachment was not confirmed. Refresh recording evidence.",
      );
    }
  }

  async function checkPracticeTransition(submission: boolean) {
    requireWriter();
    if (!accountScope || !attemptId || !online)
      throw new CandidateOutboxBlocked("attempt context unavailable");
    const active = () =>
      currentContext.current === contextKey &&
      runtimeRef.current?.status === "IN_PROGRESS";
    let barrier;
    try {
      barrier = await refreshPracticeBindings(accountScope, attemptId, active);
    } catch {
      if (active()) setPracticeOutbox(null);
      throw new CandidateOutboxBlocked("outbox state unavailable");
    }
    if (
      !active() ||
      barrier.bindingIds.size ||
      (!submission && barrier.submissionStarted)
    )
      throw new CandidateOutboxBlocked("attempt has an unresolved action");
    return barrier.submissionStarted;
  }

  async function navigateTo(bindingId: string) {
    if (
      !attemptId ||
      !editable ||
      (lidForm && (submissionUnconfirmed || submittingRef.current)) ||
      audioPendingRef.current ||
      navigatingRef.current
    )
      return;
    const expectedContext = currentContext.current;
    navigatingRef.current = true;
    setNavigating(true);
    try {
      await saveSequence.current;
      if (saveBlocked.current) throw new Error("response is not synchronized");
      if (lidForm && accountScope) {
        await withCandidateAttemptLock(accountScope, attemptId, async () => {
          await checkPracticeTransition(false);
          if (currentContext.current !== expectedContext) return;
          await candidateApi.navigate(
            attemptId,
            bindingId,
            AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
            requireWriter(),
          );
        });
      } else if (!lidForm) {
        await candidateApi.navigate(attemptId, bindingId);
      } else return;
      if (currentContext.current !== expectedContext) return;
      focusQuestion.current = true;
      await loadRuntime(attemptId, "runtime");
      setError(null);
    } catch (cause) {
      if (currentContext.current === expectedContext) {
        writerFailure(cause);
        if (isExpired(cause)) markExpired();
        else if (cause instanceof CandidateOutboxBlocked) {
          setPracticeOutbox(null);
          setError(
            "Die Antworten müssen bestätigt sein, bevor Sie die Frage wechseln können.",
          );
        } else setError(safeError(cause, Boolean(lidForm)));
      }
    } finally {
      if (currentContext.current === expectedContext) {
        navigatingRef.current = false;
        setNavigating(false);
      }
    }
  }

  async function submit() {
    if (
      !attemptId ||
      !editable ||
      audioPendingRef.current ||
      navigatingRef.current ||
      submittingRef.current
    )
      return;
    const expectedContext = contextKey;
    const scope = accountScope;
    if (lidForm && !scope) return;
    submittingRef.current = true;
    setSubmitting(true);
    submitDialogRef.current?.close();
    try {
      await saveSequence.current;
      if (
        currentContext.current !== expectedContext ||
        runtimeRef.current?.status !== "IN_PROGRESS"
      )
        return;
      if (!online || saveBlocked.current) {
        setError(
          lidForm
            ? "Synchronisieren Sie Ihre Antworten vor der Abgabe."
            : "Synchronize all responses before submitting. Reload to resolve a conflict.",
        );
        submitTriggerRef.current?.focus();
        return;
      }
      const submitted =
        lidForm && scope
          ? await withCandidateAttemptLock(scope, attemptId, async () => {
              const retry = await checkPracticeTransition(true);
              if (retry) {
                const state = await candidateApi.runtime(
                  attemptId,
                  AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
                );
                if (state.status === "EXPIRED" || state.status === "SUBMITTED")
                  return state;
                if (state.status !== "IN_PROGRESS")
                  throw new CandidateOutboxBlocked("attempt is not writable");
              }
              let submissionId: string;
              try {
                submissionId = await fenceCandidateSubmission(
                  scope,
                  attemptId,
                  requireWriter()?.generation,
                );
              } catch {
                setPracticeOutbox(null);
                throw new CandidateOutboxBlocked(
                  "submission fence unavailable",
                );
              }
              if (currentContext.current !== expectedContext)
                throw new CandidateOutboxBlocked("attempt context changed");
              setSharedSubmissionStarted(true);
              setSubmissionUnconfirmed(true);
              try {
                return await candidateApi.submit(
                  attemptId,
                  AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
                  requireWriter(),
                );
              } catch (cause) {
                if (isDefiniteSubmissionRejection(cause)) {
                  const state = await candidateApi.runtime(
                    attemptId,
                    AbortSignal.timeout(RECOVERY_TIMEOUT_MS),
                  );
                  if (currentContext.current !== expectedContext) throw cause;
                  if (
                    state.status === "IN_PROGRESS" &&
                    (await releaseRejectedCandidateSubmission(
                      scope,
                      attemptId,
                      submissionId,
                    )) &&
                    currentContext.current === expectedContext
                  ) {
                    // Recovery is complete under the lock. Repaint the
                    // authoritative responses without making another request.
                    updateRuntime(
                      (current) =>
                        current && {
                          ...current,
                          status: state.status,
                          responses: state.responses,
                        },
                    );
                    const shownBinding =
                      runtimeRef.current?.session.current_binding_id ??
                      flattenItems(state)[0]?.binding_id;
                    setResponse(
                      state.responses.find(
                        (answer) => answer.binding_id === shownBinding,
                      )?.payload ?? {},
                    );
                    setSharedSubmissionStarted(false);
                    setSubmissionUnconfirmed(false);
                    setRetrySafety(false);
                    saveBlocked.current = false;
                    setPracticeOutbox({
                      context: contextKey,
                      attemptId,
                      bindingIds: new Set(),
                    });
                    setStatus("Saved");
                    setError(
                      "Die Abgabe wurde abgelehnt. Ihre Übung ist weiterhin offen. Sie können Ihre Antworten bearbeiten und die Abgabe erneut versuchen.",
                    );
                    requestAnimationFrame(() =>
                      questionHeadingRef.current?.focus(),
                    );
                    return null;
                  }
                }
                throw cause;
              }
            })
          : await candidateApi.submit(attemptId);
      if (!submitted) return;
      if (currentContext.current !== expectedContext) return;
      updateRuntime((current) =>
        current ? { ...current, status: submitted.status } : current,
      );
      setSubmissionUnconfirmed(false);
      focusSubmission.current = Boolean(lidForm);
      setError(null);
      setStatus(submitted.status === "EXPIRED" ? "Expired" : "Submitted");
    } catch (cause) {
      if (currentContext.current === expectedContext) {
        writerFailure(cause);
        if (isExpired(cause)) {
          markExpired();
          setSubmissionUnconfirmed(false);
        } else if (cause instanceof CandidateOutboxBlocked) {
          setPracticeOutbox(null);
          setError("Synchronisieren Sie Ihre Antworten vor der Abgabe.");
        } else if (lidForm) {
          try {
            focusSubmission.current = true;
            const state = await loadRuntime(attemptId);
            if (currentContext.current !== expectedContext) return;
            if (state?.status === "SUBMITTED" || state?.status === "EXPIRED") {
              setSubmissionUnconfirmed(false);
              setError(null);
              return;
            }
          } catch {
            // The outcome stays unknown; repeating this submission is safe.
          }
          if (currentContext.current === expectedContext) {
            setError(
              "Die Abgabe ist noch nicht bestätigt. Prüfen Sie die Verbindung und versuchen Sie die Abgabe erneut.",
            );
            requestAnimationFrame(() => submitTriggerRef.current?.focus());
          }
        } else setError(safeError(cause));
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      if (lidForm && accountScope)
        notifyCandidateOutbox(accountScope, attemptId);
    }
  }

  if (
    screen === "loading" ||
    (screen === "runtime" && runtimeContext !== contextKey)
  )
    return (
      <main lang={germanPractice ? "de" : undefined}>
        <p role="status">
          {germanPractice ? "Übung wird geladen…" : "Loading exam…"}
        </p>
      </main>
    );
  if (screen === "unavailable")
    return (
      <main lang={germanPractice ? "de" : undefined}>
        <h1>{germanPractice ? "Übung nicht verfügbar" : "Exam unavailable"}</h1>
        <p role="alert" lang={germanPractice ? "de" : undefined}>
          {error ??
            (germanPractice
              ? "Diese Übung ist zurzeit nicht verfügbar."
              : "This exam is currently unavailable.")}
        </p>
        <button
          type="button"
          lang="de"
          onClick={() => {
            setError(null);
            setScreen("loading");
            setLoadRetry((value) => value + 1);
          }}
        >
          Erneut versuchen
        </button>{" "}
        <Link href="/candidate">
          {germanPractice ? "Zurück zu meinen Prüfungen" : "Back to my exams"}
        </Link>
      </main>
    );
  if (screen === "prestart") {
    const duration = runtime?.runtime_plan.duration_seconds;
    const german = Boolean(lidForm || lidAssignment);
    return (
      <main
        lang={german ? "de" : undefined}
        className={german ? styles.practice : undefined}
        style={
          german
            ? undefined
            : { maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }
        }
      >
        <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
          {german ? "Prüfungsübung" : "Candidate exam"}
        </p>
        <h1>{lidForm?.title ?? lidAssignment?.title ?? "Exam assignment"}</h1>
        {(lidForm || lidAssignment) && (
          <p lang="en">
            {lidForm?.notice ??
              lidAssignment?.notice ??
              "ORIGINAL SYNTHETIC TEST CONTENT · NOT AN OFFICIAL BAMF QUESTION"}
          </p>
        )}
        {lidForm && (
          <p>
            Gewähltes Bundesland: {lidForm.bundesland_label}. Das Bundesland
            bleibt für diesen Versuch festgelegt.
          </p>
        )}
        <p>
          {german
            ? "Die Bearbeitungszeit beginnt erst, wenn Sie die Übung starten."
            : "This pre-start screen does not start the attempt or begin the timer."}
        </p>
        <p>
          {german
            ? "Verfügbarkeit vom Server bestätigt."
            : "Availability: confirmed by the server."}
        </p>
        <p>
          {german
            ? duration
              ? `Bearbeitungszeit: ${Math.ceil(duration / 60)} Minuten`
              : "Der Server legt die Bearbeitungszeit fest."
            : `Duration: ${duration ? `${Math.ceil(duration / 60)} minutes` : "Server-defined"}`}
        </p>
        <p>
          {german
            ? "Die Fragen erscheinen, sobald der Server den Start bestätigt hat."
            : "Content is delivered only after the server confirms the start operation."}
        </p>
        <h2>{german ? "Hinweise" : "Instructions"}</h2>
        <p>
          {german
            ? "Wählen Sie zu jeder Frage eine Antwort. Der Server speichert Ihren Fortschritt."
            : "Answer each question using the supported response control. Your progress is saved by the server."}
        </p>
        {lidAssignment && !attemptId && (
          <>
            <p id="practice-state-help">
              Wählen Sie ein Bundesland für diese Übung. Bundesländer mit zu
              wenigen verfügbaren Fragen können Sie nicht auswählen.
            </p>
            <label>
              Bundesland für die Übung
              <select
                aria-describedby="practice-state-help"
                value={practiceState}
                disabled={starting}
                onChange={(event) => setPracticeState(event.target.value)}
              >
                <option value="">Bundesland auswählen</option>
                {lidAssignment.bundeslaender.map((state) => (
                  <option
                    key={state.code}
                    value={state.code}
                    disabled={!state.available}
                  >
                    {state.label}
                    {state.available ? "" : " – keine Fragen verfügbar"}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        {writerRequired && (
          <WriterPanel
            phase={writerPhase}
            generation={runtime?.writer?.generation ?? 0}
            contextKey={contextKey}
            pendingAnswers={unconfirmedBindings?.size ?? 0}
            onRetry={() => void acquireWriter()}
            onTakeover={(generation) => acquireWriter(true, generation)}
          />
        )}
        {error && <p role="alert">{error}</p>}
        {!attemptId ? (
          <button
            type="button"
            onClick={prepareAttempt}
            disabled={
              starting || Boolean(lidAssignment && !selectedPracticeState)
            }
          >
            {german
              ? starting
                ? "Übung wird vorbereitet…"
                : "Übung vorbereiten"
              : starting
                ? "Preparing exam…"
                : "Prepare exam"}
          </button>
        ) : (
          <button
            type="button"
            onClick={startAttempt}
            disabled={starting || (writerRequired && !writerOwned)}
          >
            {german
              ? starting
                ? "Übung wird gestartet…"
                : "Übung starten"
              : starting
                ? "Starting exam…"
                : "Start exam"}
          </button>
        )}
      </main>
    );
  }

  const content = (currentItem?.content ?? {}) as {
    prompt?: string;
    options?: Array<{ option_id: string; display_text?: string }>;
  };
  const promptNotice = lidForm?.notice;
  const promptWithNotice =
    promptNotice && content.prompt?.startsWith(`${promptNotice}\n\n`);
  const mode = (currentItem?.response_mode ?? "LONG_TEXT") as ResponseMode;
  const selected = (response.option_ids as string[] | undefined) ?? [];
  const textValue = (response.text as string | undefined) ?? "";
  const mediaAssetId = response.media_asset_id as string | undefined;
  const expired = runtime?.status === "EXPIRED";
  const message = expired || lidForm ? null : runtimeStateMessage(status);
  const practiceStatus: Record<RuntimeState, string> = {
    Saved: "Alle Änderungen sind vom Server bestätigt.",
    Saving: "Antwort wird gespeichert.",
    "Save failed":
      "Speicherung nicht bestätigt. Stellen Sie die Verbindung wieder her und versuchen Sie es erneut.",
    "Offline — queued":
      "Offline. Diese Antwort wartet auf diesem Gerät auf die Bestätigung durch den Server.",
    Conflict:
      "Eine neuere Antwort liegt auf dem Server. Laden Sie diese Antwort, um Ihre abgelehnte Änderung zu verwerfen.",
    Expired: "Die Bearbeitungszeit ist abgelaufen.",
    Submitted: "Der Server hat die Abgabe bestätigt.",
  };
  const practiceSaveStatus = expired
    ? "Expired"
    : pendingSave || practiceReplayPending
      ? "Saving"
      : status;
  const sharedAnswersPending = Boolean(unconfirmedBindings?.size);
  const practiceStorageUnverified = runtimeReady && !unconfirmedBindings;
  const practiceSubmissionPending =
    submissionUnconfirmed || sharedSubmissionStarted;
  const practiceControlsBlocked = Boolean(
    lidForm &&
      (!writerOwned ||
        pendingSave ||
        practiceReplayPending ||
        retrySafety ||
        practiceStorageUnverified ||
        sharedAnswersPending),
  );
  const practiceAnswersBlocked =
    practiceControlsBlocked || submitting || practiceSubmissionPending;
  const navigationBlocked =
    !editable ||
    practiceControlsBlocked ||
    practiceSubmissionPending ||
    audioPending ||
    navigating;
  const savedBindings = new Set(
    runtime?.responses.map(({ binding_id }) => binding_id),
  );
  const unansweredItems = items.filter(
    ({ binding_id }) => !savedBindings.has(binding_id),
  );
  const unanswered = unansweredItems.length;
  const confirmedOverviewCount = items.filter(
    ({ binding_id }) =>
      savedBindings.has(binding_id) && !unconfirmedBindings?.has(binding_id),
  ).length;
  const showQuestionOverview =
    lidForm &&
    runtimeReady &&
    runtime?.runtime_plan.modules.length === 1 &&
    currentItem?.navigationMode === "FREE_WITHIN_MODULE";
  return (
    <main
      lang={lidForm ? "de" : undefined}
      className={lidForm ? styles.practice : undefined}
      style={
        lidForm
          ? undefined
          : { maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }
      }
    >
      {lidForm && (
        <a className={styles.skip} href="#question-heading">
          Zur aktuellen Frage
        </a>
      )}
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        {lidForm ? "Prüfungsübung" : "Candidate exam"}
      </p>
      <h1>{lidForm?.title ?? "Runtime"}</h1>
      {lidForm && (
        <>
          <p lang="en">{lidForm.notice}</p>
          <p>Gewähltes Bundesland: {lidForm.bundesland_label}</p>
          <p>
            {lidForm.general_count + lidForm.state_count} Fragen, davon{" "}
            {lidForm.general_count} allgemeine Fragen und {lidForm.state_count}{" "}
            zum Bundesland
          </p>
        </>
      )}
      {lidForm ? (
        <p>Versuch {attemptId}</p>
      ) : (
        <p>Attempt {attemptId} · Server time controls the deadline.</p>
      )}
      {expired ? (
        <section lang="de" aria-labelledby="expired-attempt">
          <h2 id="expired-attempt">Prüfungszeit abgelaufen</h2>
          <p role="alert">
            Der Server hat diesen Versuch als abgelaufen markiert (EXPIRED).
            Ihre gespeicherten Antworten bleiben erhalten. Sie können keine
            Antworten mehr ändern oder den Versuch abgeben.
          </p>
          <p>
            <Link href="/candidate">Zurück zu meinen Prüfungen</Link>. Wenden
            Sie sich an Ihre Institution, wenn Sie Hilfe benötigen.
          </p>
        </section>
      ) : lidForm && runtime?.status === "SUBMITTED" ? (
        <h2 ref={submissionHeadingRef} tabIndex={-1}>
          Übung abgegeben
        </h2>
      ) : (
        <RuntimeTimer
          key={attemptId}
          monotonicDeadline={runtime?.monotonic_deadline ?? null}
          language={lidForm ? "de" : "en"}
          onDeadline={refreshAttempt}
        />
      )}
      {lidForm && attemptId && runtime?.status === "SUBMITTED" && (
        <LidPracticeResultPanel
          key={`${contextKey}:${attemptId}`}
          attemptId={attemptId}
          bundeslandLabel={lidForm.bundesland_label}
        />
      )}
      {!lidForm && (
        <p>
          Module and task position: {currentIndex + 1} of {items.length}
        </p>
      )}
      {writerRequired && (
        <WriterPanel
          phase={writerPhase}
          generation={runtime?.writer?.generation ?? 0}
          contextKey={contextKey}
          pendingAnswers={unconfirmedBindings?.size ?? 0}
          onRetry={() => void acquireWriter()}
          onTakeover={(generation) => acquireWriter(true, generation)}
        />
      )}
      <p
        role={lidForm ? "status" : undefined}
        lang={lidForm ? "de" : undefined}
      >
        {lidForm
          ? online
            ? "Verbindung verfügbar"
            : "Verbindung unterbrochen"
          : `Connection: ${online ? "Online" : "Offline — changes are queued"}`}
      </p>
      <p>
        {lidForm ? "Gespeicherte Antwortversion" : "Saved response revision"}:{" "}
        {savedRevision}
      </p>
      {lidForm ? (
        <>
          <p role="status" aria-atomic="true" lang="de" id={errorId}>
            {writerRequired && !writerOwned
              ? "Die Bearbeitung ist in diesem Browser noch nicht freigegeben."
              : practiceReplayPending && !online
                ? "Der Antwortstatus wird nach Wiederherstellung der Verbindung geprüft."
                : practiceStorageUnverified &&
                    !practiceReplayPending &&
                    !pendingSave
                  ? "Der Speicherstatus konnte noch nicht sicher bestätigt werden."
                  : sharedAnswersPending && practiceSaveStatus === "Saved"
                    ? "Antworten warten auf die Bestätigung durch den Server."
                    : practiceStatus[practiceSaveStatus]}
          </p>
          {editable &&
            !practiceSubmissionPending &&
            (retrySafety ||
              sharedAnswersPending ||
              practiceStorageUnverified) && (
              <button
                type="button"
                aria-disabled={!online || pendingSave || retryingSave}
                lang="de"
                onClick={() =>
                  retryPractice(rejectedPracticeOperations.current.size > 0)
                }
              >
                {rejectedPracticeOperations.current.size > 0
                  ? "Bestätigte Antwort laden"
                  : "Speicherung erneut versuchen"}
              </button>
            )}
        </>
      ) : (
        <p
          role={status === "Saved" || status === "Saving" ? "status" : "alert"}
          aria-live={
            status === "Saved" || status === "Saving" ? "off" : "polite"
          }
        >
          Autosave: {expired ? "Expired" : status}
        </p>
      )}
      {message && (
        <p id={errorId} role="alert">
          {message}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <section aria-labelledby="question-heading">
        <h2 id="question-heading" ref={questionHeadingRef} tabIndex={-1}>
          {lidForm
            ? `Frage ${currentIndex + 1} von ${items.length}`
            : "Question"}
        </h2>
        <p id={promptId}>
          {promptWithNotice ? (
            <>
              <span lang="en">{promptNotice}</span>
              {content.prompt?.slice(promptNotice.length)}
            </>
          ) : (
            (content.prompt ??
            (lidForm ? "Frage nicht verfügbar" : "Question content"))
          )}
        </p>
        <p id={responseHelpId}>
          {lidForm
            ? `Antwortformat: ${practiceModeLabels[mode]}`
            : `Response mode: ${modeLabels[mode] ?? mode}`}
        </p>
        {(mode === "SELECT_ONE" || mode === "SELECT_MULTIPLE") && (
          <fieldset
            aria-describedby={`${promptId} ${responseHelpId}${lidForm ? " " + errorId : ""}`}
          >
            <legend>
              {mode === "SELECT_ONE"
                ? lidForm
                  ? "Wählen Sie eine Antwort"
                  : "Select one answer"
                : lidForm
                  ? "Wählen Sie alle zutreffenden Antworten"
                  : "Select all answers that apply"}
            </legend>
            {(content.options ?? []).map((option) => (
              <label
                key={option.option_id}
                className={lidForm ? styles.answer : undefined}
                style={lidForm ? undefined : { display: "block" }}
              >
                <input
                  type={mode === "SELECT_ONE" ? "radio" : "checkbox"}
                  name="candidate-response"
                  checked={
                    mode === "SELECT_ONE"
                      ? response.option_id === option.option_id
                      : selected.includes(option.option_id)
                  }
                  onClick={(event) => {
                    if (
                      lidForm &&
                      (practiceAnswersBlocked || savingPracticeResponse.current)
                    )
                      event.preventDefault();
                  }}
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
                  aria-disabled={
                    lidForm && editable && practiceAnswersBlocked
                      ? true
                      : undefined
                  }
                  disabled={!editable || (!lidForm && pendingSave)}
                />{" "}
                <span>{option.display_text ?? option.option_id}</span>
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
              disabled={!editable || pendingSave}
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
              disabled={!editable || pendingSave}
              rows={8}
              maxLength={20000}
            />
          </label>
        )}
        {mode === "AUDIO" && attemptId && currentItem && (
          <AudioResponseControl
            key={`${contextKey}:${currentItem.binding_id}`}
            attemptId={attemptId}
            bindingId={currentItem.binding_id}
            editable={Boolean(editable) && !navigating}
            currentMediaAssetId={mediaAssetId}
            policy={
              currentItem.audio_policy ?? currentItem.content.audio_policy
            }
            onActivityChange={audioActivity}
            onAttachmentConfirmed={(assetId, revision) => {
              if (currentContext.current !== contextKey) return;
              updateRuntime((current) =>
                current
                  ? applySavedResponse(current, currentItem.binding_id, {
                      payload: { media_asset_id: assetId },
                      revision,
                    })
                  : current,
              );
              setResponse({ media_asset_id: assetId });
              saveBlocked.current = false;
              setStatus("Saved");
            }}
            onSaved={async (assetId, operationId) => {
              const ack = await persist(
                { media_asset_id: assetId },
                operationId,
              );
              if (!ack)
                throw new Error("The server did not confirm audio attachment.");
              return ack;
            }}
          />
        )}
      </section>
      <nav
        aria-label={
          lidForm
            ? "Navigation zwischen den Fragen"
            : "Exam question navigation"
        }
      >
        <p aria-current="step">
          {lidForm ? "Aktuelle Frage" : "Current question"} {currentIndex + 1}
        </p>
        <button
          type="button"
          onClick={() =>
            currentIndex > 0 &&
            void navigateTo(items[currentIndex - 1].binding_id)
          }
          disabled={navigationBlocked || currentIndex <= 0}
        >
          {lidForm ? "Vorherige Frage" : "Previous question"}
        </button>
        <button
          type="button"
          onClick={() =>
            currentIndex >= 0 &&
            currentIndex < items.length - 1 &&
            void navigateTo(items[currentIndex + 1].binding_id)
          }
          disabled={
            navigationBlocked ||
            currentIndex < 0 ||
            currentIndex >= items.length - 1
          }
        >
          {lidForm ? "Nächste Frage" : "Next question"}
        </button>
      </nav>
      {showQuestionOverview && (
        <details className={styles.questionOverview} lang="de">
          <summary>Fragenübersicht</summary>
          <p>
            {unconfirmedBindings
              ? `${confirmedOverviewCount} von ${items.length} Antworten gespeichert`
              : "Der Antwortstatus wird geprüft."}
          </p>
          <a href="#practice-submission">
            Fragenliste überspringen und zur Abgabe
          </a>
          <ol className={styles.questionGrid} role="list">
            {items.map(({ binding_id }, index) => {
              const isCurrent = binding_id === currentBinding;
              return (
                <li key={binding_id}>
                  <button
                    type="button"
                    aria-current={isCurrent ? "step" : undefined}
                    disabled={
                      navigationBlocked || !online || submitting || isCurrent
                    }
                    onClick={() => void navigateTo(binding_id)}
                  >
                    <span>Frage {index + 1}</span>
                    <small>
                      {!unconfirmedBindings
                        ? "Wird geprüft"
                        : unconfirmedBindings.has(binding_id)
                          ? "Nicht bestätigt"
                          : savedBindings.has(binding_id)
                            ? "Gespeichert"
                            : "Offen"}
                    </small>
                  </button>
                </li>
              );
            })}
          </ol>
        </details>
      )}
      {showQuestionOverview && (
        <h2 id="practice-submission" tabIndex={-1}>
          Abgabe
        </h2>
      )}
      <button
        type="button"
        ref={submitTriggerRef}
        aria-haspopup={lidForm ? "dialog" : undefined}
        onClick={() => {
          if (!lidForm) {
            void submit();
          } else if (submitDialogRef.current) {
            submitDialogRef.current.returnValue = "";
            submitDialogRef.current.showModal();
            cancelSubmitRef.current?.focus();
          }
        }}
        disabled={
          !editable ||
          practiceControlsBlocked ||
          pendingSave ||
          audioPending ||
          navigating ||
          submitting
        }
      >
        {lidForm
          ? practiceSubmissionPending
            ? "Abgabe erneut versuchen"
            : "Übung abgeben"
          : "Submit attempt"}
      </button>
      {lidForm && !expired && (
        <p lang="de">
          <Link href="/candidate">Zurück zu meinen Prüfungen</Link>
        </p>
      )}
      {lidForm && (
        <dialog
          ref={submitDialogRef}
          aria-labelledby={submitTitleId}
          aria-describedby={submitDescriptionId}
          onClose={(event) => {
            if (
              !event.currentTarget.open &&
              event.currentTarget.returnValue !== "question" &&
              !submittingRef.current &&
              runtimeRef.current?.status === "IN_PROGRESS"
            )
              submitTriggerRef.current?.focus();
          }}
          style={{
            maxWidth: "min(28rem, calc(100% - 2rem))",
            borderRadius: "0.5rem",
          }}
        >
          <h2 id={submitTitleId}>Möchten Sie die Übung abgeben?</h2>
          <div id={submitDescriptionId}>
            <p>Nach der Abgabe können Sie Ihre Antworten nicht mehr ändern.</p>
            <p>
              {unanswered} von {items.length} Fragen sind unbeantwortet.
            </p>
            {unanswered > 0 && !practiceSubmissionPending && (
              <p lang="de">
                Sie können zur ersten unbeantworteten Frage springen.
              </p>
            )}
          </div>
          <button
            type="button"
            ref={cancelSubmitRef}
            onClick={() => submitDialogRef.current?.close()}
          >
            Weiter bearbeiten
          </button>{" "}
          <button type="button" onClick={() => void submit()}>
            Endgültig abgeben
          </button>
          {unanswered > 0 && !practiceSubmissionPending && (
            <button
              type="button"
              lang="de"
              onClick={() => {
                const [next] = unansweredItems;
                if (next) {
                  submitDialogRef.current?.close("question");
                  if (next.binding_id === currentBinding) {
                    questionHeadingRef.current?.focus();
                  } else {
                    void navigateTo(next.binding_id);
                  }
                }
              }}
            >
              Erste unbeantwortete Frage
            </button>
          )}
        </dialog>
      )}
      {audioPending && (
        <p role="status">
          Finish or cancel the pending audio recording and attachment before
          navigating or submitting.
        </p>
      )}
    </main>
  );
}
