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
              <dt>API liveness</dt>
              <dd>{system.api?.status ?? "NOT_VERIFIED"}</dd>
              <dt>Dependency readiness</dt>
              <dd>{system.readiness?.status ?? "NOT_VERIFIED"}</dd>
              <dt>Last readiness measurement (UTC)</dt>
              <dd>
                {system.readiness ? (
                  <time dateTime={system.readiness.measured_at}>
                    {new Date(system.readiness.measured_at).toISOString()}
                  </time>
                ) : (
                  "NOT_VERIFIED"
                )}
              </dd>
              <dt>Database</dt>
              <dd>
                {[system.database.status, system.database.failure_category]
                  .filter(Boolean)
                  .join(" — ")}
              </dd>
              <dt>Object storage backend</dt>
              <dd>{system.object_storage.backend}</dd>
              <dt>Object storage health</dt>
              <dd>
                {[
                  system.object_storage.health,
                  system.object_storage.failure_category,
                ]
                  .filter(Boolean)
                  .join(" — ")}
              </dd>
              <dt>Storage guard</dt>
              <dd>{system.object_storage.guard ?? "NOT_VERIFIED"}</dd>
              <dt>Private synthetic object</dt>
              <dd>{system.object_storage.canary ?? "NOT_VERIFIED"}</dd>
              <dt>Storage provider approval</dt>
              <dd>
                {system.object_storage.provider_approval ?? "NOT_VERIFIED"}
              </dd>
              <dt>Redis health</dt>
              <dd>
                {[system.redis.health, system.redis.failure_category]
                  .filter(Boolean)
                  .join(" — ")}
              </dd>
              <dt>Alert delivery</dt>
              <dd>{system.alert_delivery ?? "NOT_VERIFIED"}</dd>
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
          <section className={css.card}>
            <h2>Execution queues</h2>
            <p>
              Worker process health:{" "}
              {system.workers?.process_health ?? "NOT_VERIFIED"}
            </p>
            {system.workers?.queues ? (
              Object.entries(system.workers.queues).map(([name, counts]) => (
                <section key={name}>
                  <h3>{name}</h3>
                  <dl className={css.health}>
                    {Object.entries(counts).map(([state, count]) => (
                      <div key={state}>
                        <dt>{state}</dt>
                        <dd>{count}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              ))
            ) : (
              <p>Queue counts are unavailable.</p>
            )}
          </section>
          <section className={css.card}>
            <h2>Observed operational failures</h2>
            <p>Counts are local to this API process and reset on restart.</p>
            {Object.entries(system.operational_failures ?? {}).map(
              ([operation, counts]) => (
                <section key={operation}>
                  <h3>{operation}</h3>
                  <dl className={css.health}>
                    {Object.entries(counts).map(([category, count]) => (
                      <div key={category}>
                        <dt>{category}</dt>
                        <dd>{count}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              ),
            )}
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
