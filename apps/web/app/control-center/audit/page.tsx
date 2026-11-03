"use client";

import { FormEvent, useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import { controlApi, type MetadataPage } from "../control-api";
import { ErrorNotice, MetadataTable, Pagination } from "../metadata";
import css from "../control-center.module.css";

export default function AuditPage() {
  const { user } = useAuth();
  const [page, setPage] = useState<MetadataPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [eventInput, setEventInput] = useState("");
  const [organisationInput, setOrganisationInput] = useState("");
  const [filters, setFilters] = useState({
    event_type: "",
    organisation_id: "",
  });
  const allowed = Boolean(
    user?.platform_permissions.includes("platform.audit.read"),
  );
  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setError(null);
    if (!allowed) return;
    void controlApi
      .page("audit", { ...filters, offset, limit: 20 })
      .then((value) => {
        if (!cancelled) setPage(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Audit activity is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, filters, offset, reload]);
  function apply(event: FormEvent) {
    event.preventDefault();
    setFilters({
      event_type: eventInput.trim(),
      organisation_id: organisationInput.trim(),
    });
    setOffset(0);
  }
  return (
    <>
      <p className={css.eyebrow}>Append-only governance evidence</p>
      <h1>Audit evidence</h1>
      <p className={css.intro}>
        Inspect event metadata and correlation references. Private payloads,
        answers, notes, prompts, credentials and free-form reasons are excluded.
        This view cannot change audit history.
      </p>
      {allowed && (
        <form className={css.filters} onSubmit={apply}>
          <label>
            Exact event type
            <input
              value={eventInput}
              onChange={(event) => setEventInput(event.target.value)}
              maxLength={120}
              placeholder="candidate_audio.finalized"
            />
          </label>
          <label>
            Organisation reference
            <input
              value={organisationInput}
              onChange={(event) => setOrganisationInput(event.target.value)}
              pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
              placeholder="Optional UUID"
            />
          </label>
          <button className={css.button}>Apply filters</button>
          {(filters.event_type || filters.organisation_id) && (
            <button
              className={css.secondary}
              type="button"
              onClick={() => {
                setEventInput("");
                setOrganisationInput("");
                setFilters({ event_type: "", organisation_id: "" });
                setOffset(0);
              }}
            >
              Clear filters
            </button>
          )}
        </form>
      )}
      {!allowed ? (
        <p className={css.error} role="alert">
          Platform audit permission is required.
        </p>
      ) : error ? (
        <ErrorNotice
          message={error}
          retry={() => setReload((value) => value + 1)}
        />
      ) : !page ? (
        <p role="status">Loading audit evidence…</p>
      ) : (
        <>
          {!page.items.length ? (
            <p className={css.notice}>No audit events match these filters.</p>
          ) : (
            <MetadataTable
              caption="Audit events"
              columns={[
                ["timestamp", "Timestamp"],
                ["event_type", "Event type"],
                ["actor_type", "Actor type"],
                ["actor_id", "Actor reference"],
                ["organisation_id", "Organisation reference"],
                ["entity_type", "Entity type"],
                ["entity_id", "Entity reference"],
                ["operation_id", "Operation reference"],
                ["source_version_id", "Source assessment version"],
                ["request_id", "Request reference"],
                ["workflow_id", "Workflow reference"],
              ]}
              rows={page.items}
            />
          )}
          <Pagination page={page} setOffset={setOffset} />
        </>
      )}
    </>
  );
}
