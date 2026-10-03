"use client";

import React, { useEffect, useState } from "react";

export type ResponseMode =
  | "SELECT_ONE"
  | "SELECT_MULTIPLE"
  | "TEXT"
  | "LONG_TEXT";
export type RuntimeState =
  | "Saved"
  | "Saving"
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
  if (status === "Conflict") {
    return "A newer response exists on the server. Your change was not applied. Reload the latest response before continuing.";
  }
  if (status === "Expired") {
    return "This attempt is no longer writable. Any unsynced local response was not accepted by the server.";
  }
  return null;
}

export function RuntimeTimer({ deadlineAt }: { deadlineAt: number | null }) {
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const [milestone, setMilestone] = useState<string | null>(null);

  useEffect(() => {
    if (deadlineAt === null) {
      setRemainingSeconds(null);
      return;
    }
    const update = () =>
      setRemainingSeconds(
        Math.max(0, Math.ceil((deadlineAt - Date.now()) / 1000)),
      );
    update();
    const interval = window.setInterval(update, 1000);
    return () => window.clearInterval(interval);
  }, [deadlineAt]);

  useEffect(() => {
    if (remainingSeconds === null) return;
    const thresholds: Record<number, string> = {
      600: "10 minutes remaining",
      300: "5 minutes remaining",
      60: "1 minute remaining",
    };
    const threshold = Object.keys(thresholds)
      .map(Number)
      .find((value) => remainingSeconds === value);
    if (threshold !== undefined) setMilestone(thresholds[threshold]);
  }, [remainingSeconds]);

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
      <p style={visuallyHidden} aria-live="polite">
        {milestone}
      </p>
      <p>
        Timing is controlled by the server. This display is only a local
        estimate.
      </p>
    </section>
  );
}
