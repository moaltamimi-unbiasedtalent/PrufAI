"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { authenticatedFetch } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import css from "../control-center/control-center.module.css";

type Case = {
  id: string;
  request_type: string;
  purpose: string;
  status: string;
  identity: string;
  case_checksum: string;
  scope_checksum: string | null;
  categories: string[];
  controller_context: string;
  controller_authorization: string;
};

type Detail = Case & {
  retention: string;
  policies: Record<
    string,
    { version: number; checksum: string; status: string }
  >;
  hold_checksum: string | null;
};

type Coverage = {
  category: string;
  classification: string;
  coverage: string;
  count: number | null;
  complete: boolean;
  retention_policy: string;
  draft_version: number | null;
  relationships: string[];
  object_classes?: Record<string, Record<string, number>>;
};

type Preview = {
  eligibility: string;
  hold: string;
  dependencies: string[];
  categories: Coverage[];
};

function describe(value: string) {
  return value.toLowerCase().replaceAll("_", " ");
}

class PrivacyAccessError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (response.status === 401 || response.status === 403)
    throw new PrivacyAccessError(
      "Access denied or unconfigured. A current, dedicated privacy grant is required.",
    );
  if (response.status === 409)
    throw new Error(
      "The case evidence or policy changed. Reopen the case and retry.",
    );
  if (!response.ok) throw new Error("The privacy workbench is unavailable.");
  return (await response.json()) as T;
}

function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "The privacy workbench is unavailable.";
}

function CaseDetail({ path }: { path: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void request<Detail>(path)
      .then((value) => {
        if (current) setDetail(value);
      })
      .catch((cause: unknown) => {
        if (current) setError(message(cause));
      });
    return () => {
      current = false;
    };
  }, [path]);

  async function runPreview() {
    if (!detail?.scope_checksum) return;
    setBusy(true);
    setError(null);
    setPreview(null);
    try {
      const value = await request<Preview>(path + "/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_id: crypto.randomUUID(),
          case_checksum: detail.case_checksum,
          scope_checksum: detail.scope_checksum,
          policy_pins: Object.fromEntries(
            Object.entries(detail.policies).map(([key, policy]) => [
              key,
              policy.checksum,
            ]),
          ),
          hold_checksum: detail.hold_checksum,
        }),
      });
      setPreview(value);
    } catch (cause) {
      if (cause instanceof PrivacyAccessError) setDetail(null);
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Selected privacy case">
      {error && <p role="alert">{error}</p>}
      {!detail && !error && <p role="status">Loading case…</p>}
      {detail && (
        <>
          <h2>Case details</h2>
          <dl>
            <dt>Identity verification</dt>
            <dd>{describe(detail.identity)}</dd>
            <dt>Case status</dt>
            <dd>{describe(detail.status)}</dd>
            <dt>Controller or processor context</dt>
            <dd>
              {describe(detail.controller_context)} · authorization{" "}
              {describe(detail.controller_authorization)}
            </dd>
            <dt>Retention policy</dt>
            <dd>{detail.retention}</dd>
            <dt>Requested data categories</dt>
            <dd>{detail.categories.map(describe).join(", ")}</dd>
          </dl>
          {Object.entries(detail.policies).length > 0 && (
            <ul aria-label="Draft retention policies">
              {Object.entries(detail.policies).map(([name, policy]) => (
                <li key={name}>
                  {describe(name)} · draft version {policy.version}
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            disabled={!detail.scope_checksum || busy}
            onClick={() => void runPreview()}
          >
            {busy ? "Running discovery…" : "Run read-only preview"}
          </button>
        </>
      )}
      {preview && (
        <section aria-label="Read-only eligibility preview">
          <h2>Eligibility preview</h2>
          <p role="status">{preview.eligibility}</p>
          <p>
            Human legal and privacy review required. This preview carries no
            erasure authorization.
          </p>
          <p>Hold assessment · {describe(preview.hold)}</p>
          {preview.dependencies.length > 0 && (
            <ul aria-label="Operational dependencies">
              {preview.dependencies.map((code) => (
                <li key={code}>{describe(code)}</li>
              ))}
            </ul>
          )}
          <ul aria-label="Category discovery">
            {preview.categories.map((category) => (
              <li key={category.category}>
                <strong>{describe(category.category)}</strong>
                {" · "}
                {category.count === null
                  ? "Count unavailable"
                  : `${category.count} records`}
                {" · "}
                {describe(category.coverage)}
                {" · "}
                {describe(category.classification)}
                {" · retention "}
                {describe(category.retention_policy)}
                {category.draft_version !== null &&
                  ` · draft version ${category.draft_version}`}
                {category.relationships.length > 0 &&
                  ` · linked to ${category.relationships.map(describe).join(", ")}`}
                {Object.entries(category.object_classes ?? {}).map(
                  ([name, states]) => (
                    <ul key={name} aria-label={describe(name)}>
                      {Object.entries(states).map(([state, count]) => (
                        <li key={state}>
                          {describe(state)} · {count}
                        </li>
                      ))}
                    </ul>
                  ),
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

function Workbench({ organisation }: { organisation: string }) {
  const path = `/api/v1/privacy/organisations/${organisation}/cases`;
  const [cases, setCases] = useState<Case[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void request<{ items: Case[]; next: string | null }>(
      after ? `${path}?after=${encodeURIComponent(after)}` : path,
    )
      .then((value) => {
        if (!current) return;
        setCases(value.items);
        setNext(value.next);
      })
      .catch((cause: unknown) => {
        if (current) setError(message(cause));
      });
    return () => {
      current = false;
    };
  }, [path, after]);

  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!cases && !error && <p role="status">Loading authorized cases…</p>}
      {cases?.length === 0 && (
        <p>No privacy cases are assigned to this scope.</p>
      )}
      {cases && cases.length > 0 && (
        <ul aria-label="Authorized cases">
          {cases.map((row) => (
            <li key={row.id}>
              <button type="button" onClick={() => setSelected(row.id)}>
                Open {describe(row.request_type)} case {row.id.slice(0, 8)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {next && (
        <button
          type="button"
          onClick={() => {
            setSelected(null);
            setCases(null);
            setNext(null);
            setError(null);
            setAfter(next);
          }}
        >
          Next page
        </button>
      )}
      {selected && <CaseDetail key={selected} path={path + "/" + selected} />}
    </>
  );
}

export default function PrivacyWorkbench() {
  const { user, organisationId, loading } = useAuth();
  return (
    <main className={css.workspace}>
      <section className={css.main}>
        <h1>Privacy workbench</h1>
        <p>Governed case discovery for the selected organisation.</p>
        {loading ? (
          <p role="status">Loading secure access…</p>
        ) : !user ? (
          <Link href="/login">Sign in</Link>
        ) : !organisationId ? (
          <p role="alert">
            Privacy access is unconfigured for this session. An authorised
            organisation is required.
          </p>
        ) : (
          <Workbench
            key={user.id + ":" + organisationId}
            organisation={organisationId}
          />
        )}
      </section>
    </main>
  );
}
