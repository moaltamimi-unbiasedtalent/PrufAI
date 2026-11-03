"use client";

import { useState } from "react";
import { useAuth } from "../../lib/auth";
import {
  queueCandidateOperation,
  syncCandidateOutbox,
} from "../candidate/outbox";

const PROBE_ATTEMPT = "development-privacy-check";

// Only mounted by the server-gated development operator guide. This uses the
// actual IndexedDB queue, with a clearly synthetic operation that can never be
// synchronized as an exam response (the runtime filters by its real attempt ID).
export function OfflinePrivacyCheck() {
  const { user, organisationId, loading, demoMode } = useAuth();
  const [message, setMessage] = useState("");
  if (!demoMode) return null;
  const scope =
    user && organisationId ? { userId: user.id, organisationId } : null;
  async function inspect() {
    if (!scope) return;
    const result = await syncCandidateOutbox(
      scope,
      async () => "conflict",
      PROBE_ATTEMPT,
    );
    setMessage(`Synthetic offline checks in this account: ${result.conflicts}`);
  }
  async function queue() {
    if (!scope) return;
    await queueCandidateOperation(scope, {
      attemptId: PROBE_ATTEMPT,
      bindingId: "synthetic-probe",
      operationId: crypto.randomUUID(),
      expectedRevision: 0,
      payload: { check: "synthetic account isolation check" },
      queuedAt: new Date().toISOString(),
    });
    await inspect();
  }
  const run = (operation: () => Promise<void>) => {
    void operation().catch(() =>
      setMessage(
        "Offline storage is unavailable or the account changed. No other account data was read.",
      ),
    );
  };
  return (
    <section
      style={{
        border: "1px solid #dce2ee",
        padding: "1rem",
        marginTop: "1.5rem",
      }}
    >
      <h2>Development offline privacy check</h2>
      <p>
        This diagnostic writes a synthetic check to the real browser queue. It
        never submits an exam response. Queue a check, sign out, sign in as
        another account and inspect: the count must be zero. Signing back into
        the original account must also show zero after logout cleanup.
      </p>
      <p>Account: {user?.email ?? "Sign in first"}</p>
      <button disabled={loading || !scope} onClick={() => run(queue)}>
        Queue synthetic offline check
      </button>{" "}
      <button disabled={loading || !scope} onClick={() => run(inspect)}>
        Inspect this account queue
      </button>
      <p role="status">{message}</p>
    </section>
  );
}
