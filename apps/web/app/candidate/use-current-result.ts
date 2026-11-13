"use client";

import { useEffect, useState } from "react";
import { flushSync } from "react-dom";
import { authenticatedFetch } from "../../lib/api";

const CHECK_INTERVAL = 2000;

// Release projections are snapshots. Check the exact version again before
// rendering a snapshot and while it is visible. Never restore a cached page's
// score or statement until the server reauthorizes it.
export function useCurrentResult(
  restricted: boolean,
  attemptId: string | null | undefined,
  versionId: string | null | undefined,
): boolean {
  const identity = attemptId && versionId ? `${attemptId}:${versionId}` : null;
  const [verified, setVerified] = useState<string | null>(null);
  useEffect(() => {
    if (!restricted || !attemptId || !versionId || !identity) return;
    let request: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let revoked = false;
    let validUntil = 0;
    function invalidate() {
      request?.abort();
      validUntil = 0;
      clearTimeout(timer);
      flushSync(() => setVerified(null));
    }
    async function check() {
      clearTimeout(timer);
      request?.abort();
      if (
        revoked ||
        !navigator.onLine ||
        document.visibilityState !== "visible"
      )
        return;
      if (performance.now() >= validUntil) setVerified(null);
      const pending = new AbortController();
      request = pending;
      const started = performance.now();
      const expiry = setTimeout(
        () => {
          if (!pending.signal.aborted && performance.now() >= validUntil)
            setVerified(null);
        },
        Math.max(0, validUntil - performance.now()),
      );
      try {
        const response = await authenticatedFetch(
          `/api/v1/candidate/exam-attempts/${attemptId}/lid-practice-result/status`,
          {
            signal: AbortSignal.any([
              pending.signal,
              AbortSignal.timeout(CHECK_INTERVAL),
            ]),
          },
        );
        const status = response.ok
          ? ((await response.json()) as { result_version_id: string })
          : null;
        if (pending.signal.aborted) return;
        if (
          response.status === 404 ||
          (status && status.result_version_id !== versionId)
        )
          revoked = true;
        const allowed = status?.result_version_id === versionId;
        validUntil = allowed ? started + CHECK_INTERVAL + 250 : 0;
        setVerified(allowed ? identity : null);
      } catch {
        if (!pending.signal.aborted) setVerified(null);
      } finally {
        clearTimeout(expiry);
        if (!revoked && !pending.signal.aborted)
          timer = setTimeout(
            check,
            Math.max(0, started + CHECK_INTERVAL - performance.now()),
          );
      }
    }
    function refresh() {
      invalidate();
      void check();
    }
    void check();
    window.addEventListener("pagehide", invalidate);
    window.addEventListener("pageshow", refresh);
    window.addEventListener("offline", invalidate);
    window.addEventListener("online", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      request?.abort();
      clearTimeout(timer);
      window.removeEventListener("pagehide", invalidate);
      window.removeEventListener("pageshow", refresh);
      window.removeEventListener("offline", invalidate);
      window.removeEventListener("online", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [attemptId, identity, restricted, versionId]);
  return !restricted || (identity !== null && verified === identity);
}
