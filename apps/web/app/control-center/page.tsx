"use client";

import Link from "next/link";
import React from "react";
import { pilotActive, pilotRouteAllowed } from "../../lib/access";
import { useAuth } from "../../lib/auth";
import { ErrorNotice, MetadataTable } from "./metadata";
import { usePlatformOverview } from "./shell";
import css from "./control-center.module.css";

const metrics = [
  ["organisations", "Organisations", "/control-center/organisations"],
  ["users", "Registered users", null],
  ["standard_packs", "Standard Packs", "/control-center/standards"],
  [
    "question_bank_items",
    "Shared Question Bank items",
    "/control-center/question-bank",
  ],
  ["exam_versions", "Shared exam versions", "/control-center/exams"],
  ["open_governance_cases", "Open governance cases", null],
  [
    "recent_audit_events",
    "Audit events · last 24 hours",
    "/control-center/audit",
  ],
] as const;

export default function ControlCentreOverviewPage() {
  const { overview, error, refresh } = usePlatformOverview();
  const { user } = useAuth();
  const restricted = pilotActive(user);
  return (
    <>
      <p className={css.eyebrow}>PrüfAI platform governance</p>
      <h1>Control Centre overview</h1>
      <p className={css.intro}>
        Inspect the governed assessment platform. Counts come from authorized
        database queries; unavailable metrics are shown without a value. This
        workspace is read-only.
      </p>
      {restricted && (
        <Link href="/control-center/standards/lid">
          Inspect the Berlin LiD blueprint
        </Link>
      )}
      {error ? (
        <ErrorNotice message={error} retry={refresh} />
      ) : !overview ? (
        <p role="status">Loading authorized platform metrics…</p>
      ) : (
        <div className={css.grid}>
          {metrics
            .filter(
              ([, , href]) => !restricted || !href || pilotRouteAllowed(href),
            )
            .map(([key, label, href]) => (
              <article className={css.card} key={key}>
                <h2>{label}</h2>
                <p className={css.metric}>{overview.metrics[key] ?? "—"}</p>
                {overview.metrics[key] === null ||
                overview.metrics[key] === undefined ? (
                  <p>Not available for your platform permissions.</p>
                ) : href &&
                  overview.available_sections.includes(
                    href.split("/").at(-1) ?? "",
                  ) ? (
                  <Link href={href}>Inspect metadata →</Link>
                ) : (
                  <p>Aggregate metadata only.</p>
                )}
              </article>
            ))}
        </div>
      )}
      <h2>Recent audit activity</h2>
      {!overview ? (
        <p>Recent activity has not been loaded.</p>
      ) : !overview.recent_audit ? (
        <p className={css.notice}>
          Platform audit permission is required to inspect activity.
        </p>
      ) : !overview.recent_audit.length ? (
        <p className={css.notice}>No audit events are available.</p>
      ) : (
        <MetadataTable
          caption="Recent audit events"
          columns={[
            ["timestamp", "Time"],
            ["event_type", "Event"],
            ["entity_type", "Entity type"],
            ["entity_id", "Entity reference"],
            ["organisation_id", "Organisation reference"],
          ]}
          rows={overview.recent_audit}
        />
      )}
      {overview?.recent_audit && (
        <p>
          <Link href="/control-center/audit">
            Open filtered audit history →
          </Link>
        </p>
      )}
      <h2>Service health</h2>
      {!overview ? (
        <p>Service health has not been loaded.</p>
      ) : !overview.service_health ? (
        <p className={css.notice}>
          Platform system permission is required to inspect service health.
        </p>
      ) : (
        <section className={css.card}>
          <dl className={css.health}>
            <dt>Control Centre API</dt>
            <dd>Responding to this metadata request</dd>
            <dt>Database</dt>
            <dd>{overview.service_health.database.status}</dd>
            <dt>Object storage</dt>
            <dd>{overview.service_health.object_storage.health}</dd>
            <dt>Redis</dt>
            <dd>{overview.service_health.redis.health}</dd>
            {!restricted && (
              <>
                <dt>AI executor</dt>
                <dd>{overview.service_health.ai_executor.status}</dd>
              </>
            )}
          </dl>
          <p>
            <Link href="/control-center/system">
              Inspect service configuration →
            </Link>
          </p>
        </section>
      )}
      <p className={css.notice}>
        {user?.platform_permissions.includes("platform.system.read") && (
          <>
            Service health is reported separately in{" "}
            <Link href="/control-center/system">System health</Link>.{" "}
          </>
        )}
        A recorded AI run or provider-aligned Standard Pack does not imply live
        AI execution or certification authority.
      </p>
    </>
  );
}
