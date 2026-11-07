"use client";

import React, { useEffect, useRef, useState } from "react";
import type { RuntimeState as RuntimeDto } from "../../api";

export function applySavedResponse(
  runtime: RuntimeDto,
  bindingId: string,
  saved: { revision: number; payload: Record<string, unknown> },
): RuntimeDto {
  const response = {
    binding_id: bindingId,
    revision: saved.revision,
    payload: saved.payload,
  };
  const exists = runtime.responses.some(
    (item) => item.binding_id === bindingId,
  );
  return {
    ...runtime,
    responses: exists
      ? runtime.responses.map((item) =>
          item.binding_id === bindingId ? response : item,
        )
      : [...runtime.responses, response],
  };
}

export type ResponseMode =
  | "SELECT_ONE"
  | "SELECT_MULTIPLE"
  | "TEXT"
  | "LONG_TEXT"
  | "AUDIO";
export type RuntimeState =
  | "Saved"
  | "Saving"
  | "Save failed"
  | "Offline — queued"
  | "Conflict"
  | "Expired"
  | "Submitted";

const visuallyHidden: React.CSSProperties = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: 0,
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export function formatRemaining(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

export function runtimeStateMessage(status: RuntimeState): string | null {
  if (status === "Save failed") {
    return "The server has not confirmed this response. Keep the page open and retry before submitting.";
  }
  if (status === "Conflict") {
    return "A newer response exists on the server. Your change was not applied. Reload the latest response before continuing.";
  }
  if (status === "Expired") {
    return "This attempt is no longer writable. Any unsynced local response was not accepted by the server.";
  }
  return null;
}

const timerMilestones = [
  [0, "Time is up. Checking the server status."],
  [60, "1 minute remaining"],
  [300, "5 minutes remaining"],
  [600, "10 minutes remaining"],
] as const;

export function crossedTimerMilestone(previous: number, current: number) {
  return timerMilestones.find(
    ([seconds]) => previous > seconds && current <= seconds,
  )?.[1];
}

export function RuntimeTimer({
  monotonicDeadline,
  onDeadline,
}: {
  monotonicDeadline: number | null;
  onDeadline?: () => void;
}) {
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const [milestone, setMilestone] = useState<string | null>(null);
  const previous = useRef<number | null>(null);
  const atZero = useRef(false);

  useEffect(() => {
    if (monotonicDeadline === null) {
      setRemainingSeconds(null);
      return;
    }
    const update = () => {
      const remaining = Math.max(
        0,
        Math.ceil((monotonicDeadline - performance.now()) / 1000),
      );
      const earlier = previous.current;
      if (earlier === null || remaining < earlier) previous.current = remaining;
      const announcement =
        earlier === null
          ? undefined
          : crossedTimerMilestone(earlier, remaining);
      if (announcement) setMilestone(announcement);
      if (remaining === 0 && !atZero.current) onDeadline?.();
      atZero.current = remaining === 0;
      setRemainingSeconds(remaining);
    };
    update();
    const interval = window.setInterval(update, 1000);
    document.addEventListener("visibilitychange", update);
    window.addEventListener("focus", update);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", update);
      window.removeEventListener("focus", update);
    };
  }, [monotonicDeadline, onDeadline]);

  const label =
    remainingSeconds === null
      ? "Remaining time: waiting for the server deadline"
      : `Remaining time: ${formatRemaining(remainingSeconds)}`;

  return (
    <section aria-labelledby="time-heading">
      <h2 id="time-heading">Time remaining</h2>
      <p>
        <time
          aria-label={label}
          dateTime={
            remainingSeconds === null ? undefined : `PT${remainingSeconds}S`
          }
        >
          {remainingSeconds === null
            ? "Waiting for server deadline"
            : formatRemaining(remainingSeconds)}
        </time>
      </p>
      <p style={visuallyHidden} aria-live="polite" aria-atomic="true">
        {milestone}
      </p>
      <p>
        Timing is controlled by the server. This display is only a local
        estimate.
      </p>
    </section>
  );
}

export { AudioResponseControl } from "./audio-response-control";
