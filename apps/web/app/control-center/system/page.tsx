"use client";

import { useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import { controlApi, type PlatformSystem } from "../control-api";
import { ErrorNotice } from "../metadata";
import css from "../control-center.module.css";

export default function SystemPage() {
  const { user } = useAuth();
  const [system, setSystem] = useState<PlatformSystem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const allowed = Boolean(
    user?.platform_permissions.includes("platform.system.read"),
  );
  useEffect(() => {
    let cancelled = false;
    setSystem(null);
    setError(null);
    if (!allowed) return;
    void controlApi
      .system()
      .then((value) => {
        if (!cancelled) setSystem(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "System status is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, reload]);
  return (
    <>
      <p className={css.eyebrow}>Read-only platform operations</p>
      <h1>System health</h1>
      <p className={css.intro}>
        Environment and configured service metadata come from the API.
        Unverified services are not presented as healthy. Infrastructure
        endpoints and credentials are excluded.
      </p>
      {!allowed ? (
        <p className={css.error} role="alert">
          Platform system permission is required.
        </p>
      ) : error ? (
        <ErrorNotice
          message={error}
          retry={() => setReload((value) => value + 1)}
        />
      ) : !system ? (
        <p role="status">Loading system status…</p>
      ) : (
        <>
          <section className={css.card}>
            <h2>Service status</h2>
            <dl className={css.health}>
              <dt>Environment</dt>
              <dd>{system.environment}</dd>
              <dt>Database</dt>
              <dd>{system.database.status}</dd>
              <dt>Object storage backend</dt>
              <dd>{system.object_storage.backend}</dd>
              <dt>Object storage health</dt>
              <dd>{system.object_storage.health}</dd>
              <dt>Redis health</dt>
              <dd>{system.redis.health}</dd>
              <dt>AI executor</dt>
              <dd>
                {system.ai_executor.status} — {system.ai_executor.description}
              </dd>
              <dt>Speech transcription executor</dt>
              <dd>
                {system.speech_transcription_executor
                  ? `${system.speech_transcription_executor.status} — ${system.speech_transcription_executor.description}`
                  : "NOT VERIFIED"}
              </dd>
              <dt>Writing proposal executor</dt>
              <dd>
                {system.writing_proposal_executor
                  ? `${system.writing_proposal_executor.status} — ${system.writing_proposal_executor.description}`
                  : "NOT VERIFIED"}
              </dd>
              <dt>Speaking proposal executor</dt>
              <dd>
                {system.speaking_proposal_executor
                  ? `${system.speaking_proposal_executor.status} — ${system.speaking_proposal_executor.description}`
                  : "NOT VERIFIED"}
              </dd>
              <dt>Official issuance</dt>
              <dd>{system.certification.official_issuance}</dd>
              <dt>Nonofficial synthetic issuance setting</dt>
              <dd>
                {system.certification.synthetic_issuance_enabled
                  ? "Enabled; explicit scoped authorization remains required"
                  : "Disabled"}
              </dd>
            </dl>
          </section>
          <p className={css.notice}>
            NOT_VERIFIED means no successful live service probe is available for
            that dependency in this view. UNCONFIGURED means the platform has no
            connected execution path to demonstrate here.
          </p>
        </>
      )}
    </>
  );
}
