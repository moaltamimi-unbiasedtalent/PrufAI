"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { styles } from "../../lib/auth";
import css from "../control-center/control-center.module.css";
import {
  getReviewGrants,
  getReviewSources,
  inspectReviewSource,
  ReviewRequestError,
  submitReviewDecision,
  type ReviewDecision,
  type ReviewGrant,
  type ReviewSource,
  type ReviewSubmission,
} from "./review-api";

const decisionLabels: Record<ReviewDecision, string> = {
  METADATA_ONLY: "Metadata only",
  DENY: "Deny content use",
};
const berlinDate = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeZone: "Europe/Berlin",
});

function editionDate(value: string | null) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? berlinDate.format(date)
    : "Unknown";
}

function sourceTitle(source: ReviewSource) {
  return source.metadata.title || source.version_id;
}

function SourceDetails({
  source,
  busy,
  onSubmit,
}: {
  source: ReviewSource;
  busy: boolean;
  onSubmit: (submission: ReviewSubmission & { operation_id: string }) => void;
}) {
  const operation = useRef<{ input: string; id: string } | null>(null);
  const { metadata, allowed_decisions: decisions } = source;
  const fields = [
    ["Publisher", metadata.publisher],
    ["Edition", metadata.version_label],
    [
      "Publication date",
      metadata.publication_date === undefined
        ? undefined
        : editionDate(metadata.publication_date),
    ],
    [
      "Effective from (Europe/Berlin)",
      metadata.effective_from === undefined
        ? undefined
        : editionDate(metadata.effective_from),
    ],
    [
      "Effective until (Europe/Berlin)",
      metadata.effective_until === undefined
        ? undefined
        : editionDate(metadata.effective_until),
    ],
    ["Rights status", metadata.rights_status],
    ["Decision checksum", source.decision_checksum],
  ] as const;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const decision = String(form.get("decision"));
    if (decision !== "METADATA_ONLY" && decision !== "DENY") return;
    if (!decisions.includes(decision)) return;
    const input = {
      expected_metadata_checksum: source.metadata_checksum,
      decision,
      basis_reference: String(form.get("basis_reference")).trim(),
      rights_holder: String(form.get("rights_holder")).trim(),
      licence_type: String(form.get("licence_type")).trim(),
      retention_policy: String(form.get("retention_policy")).trim(),
    } satisfies ReviewSubmission;
    const serialised = JSON.stringify(input);
    if (operation.current?.input !== serialised) {
      operation.current = { input: serialised, id: crypto.randomUUID() };
    }
    onSubmit({ ...input, operation_id: operation.current.id });
  }

  return (
    <article className={css.card} aria-label="Source version details">
      <h3>Source version details</h3>
      {metadata.title && <p>{metadata.title}</p>}
      <dl style={{ overflowWrap: "anywhere" }}>
        <dt>Source version ID</dt>
        <dd>{source.version_id}</dd>
        <dt>Metadata SHA-256</dt>
        <dd>{source.metadata_checksum}</dd>
        {fields.map(([name, value]) =>
          value === undefined ? null : (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value ?? "Unknown"}</dd>
            </div>
          ),
        )}
      </dl>
      {decisions.length > 0 && (
        <>
          <p>
            If evidence is incomplete, leave this source unreviewed. A denial is
            a rights decision.
          </p>
          <form className={css.filters} onSubmit={submit}>
            <label>
              Rights decision
              <select name="decision" required defaultValue="" disabled={busy}>
                <option value="">Choose a decision</option>
                {decisions.map((decision) => (
                  <option key={decision} value={decision}>
                    {decisionLabels[decision]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rights holder
              <input
                name="rights_holder"
                required
                maxLength={300}
                disabled={busy}
              />
            </label>
            <label>
              Licence type
              <input
                name="licence_type"
                required
                maxLength={120}
                disabled={busy}
              />
            </label>
            <label>
              Rights basis reference
              <input
                name="basis_reference"
                required
                maxLength={240}
                disabled={busy}
              />
            </label>
            <label>
              Retention policy reference
              <input
                name="retention_policy"
                required
                maxLength={240}
                disabled={busy}
              />
            </label>
            <button className={css.button} disabled={busy}>
              Record rights decision
            </button>
          </form>
        </>
      )}
    </article>
  );
}

function GrantReview({ grant }: { grant: ReviewGrant }) {
  const [sources, setSources] = useState<ReviewSource[] | null>(null);
  const [source, setSource] = useState<ReviewSource | null>(null);
  const [ended, setEnded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void getReviewSources(grant, controller.signal)
      .then((scope) => {
        if (!controller.signal.aborted) setSources(scope.sources);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof ReviewRequestError
              ? cause.message
              : "The granted sources could not be loaded.",
          );
      });
    return () => controller.abort();
  }, [grant]);

  useEffect(() => {
    const expiry = Date.parse(grant.expires_at);
    let timeout: ReturnType<typeof setTimeout> | undefined;
    function checkExpiry() {
      const remaining = expiry - Date.now();
      if (remaining > 0) {
        timeout = setTimeout(checkExpiry, Math.min(remaining, 2_147_483_647));
      } else {
        setEnded(true);
      }
    }
    checkExpiry();
    return () => clearTimeout(timeout);
  }, [grant.expires_at]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (cause) {
      if (cause instanceof ReviewRequestError) {
        if (cause.status === 403 || cause.status === 404) setEnded(true);
        if (cause.status === 409) setSource(null);
        setError(cause.message);
      } else {
        setError("The source-review request could not be completed.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (ended)
    return (
      <p className={css.notice} role="status">
        This source-review access is unavailable.
      </p>
    );

  const pending = sources?.filter(
    (item) => item.metadata.rights_status === "NOT_REVIEWED",
  ).length;

  return (
    <section aria-label="Granted source versions">
      <h2>Granted source versions</h2>
      <p>
        Access expires{" "}
        <time dateTime={grant.expires_at}>
          {new Date(grant.expires_at).toLocaleString("en-GB", {
            timeZone: "UTC",
          })}{" "}
          UTC
        </time>
        .
      </p>
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      {message && (
        <p role="status" className={css.notice}>
          {message}
        </p>
      )}
      {sources ? (
        <>
          {sources.every(
            (item) => item.metadata.rights_status !== undefined,
          ) && <p>{pending} visible sources remain unreviewed.</p>}
          {sources.length === 0 ? (
            <p>No source versions are currently available for this grant.</p>
          ) : (
            <ul>
              {sources.map((item) => (
                <li key={item.version_id} style={{ marginBlock: "0.65rem" }}>
                  <button
                    className={css.secondary}
                    disabled={busy}
                    onClick={() => {
                      setSource(null);
                      void perform(async () =>
                        setSource(
                          await inspectReviewSource(grant, item.version_id),
                        ),
                      );
                    }}
                  >
                    Inspect {sourceTitle(item)}
                  </button>{" "}
                  {item.metadata.version_label}
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        !error && <p role="status">Loading granted source versions…</p>
      )}
      {source && (
        <SourceDetails
          key={`${source.version_id}:${source.decision_checksum ?? "pending"}`}
          source={source}
          busy={busy}
          onSubmit={(submission) => {
            void perform(async () => {
              const reviewed = await submitReviewDecision(
                grant,
                source.version_id,
                submission,
              );
              setSource(reviewed);
              setSources(
                (previous) =>
                  previous?.map((item) =>
                    item.version_id === reviewed.version_id ? reviewed : item,
                  ) ?? null,
              );
              setMessage("Rights decision recorded.");
            });
          }}
        />
      )}
    </section>
  );
}

export default function SourceReviewPage() {
  const [grants, setGrants] = useState<ReviewGrant[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void getReviewGrants(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setGrants(items);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError("Source-review access could not be loaded.");
      });
    return () => controller.abort();
  }, []);

  return (
    <main>
      <p style={styles.kicker}>Independent standards review</p>
      <h1>Scoped source review</h1>
      <p>Inspect an exact source edition before recording a rights decision.</p>
      {error ? (
        <p role="alert" className={css.error}>
          {error}
        </p>
      ) : grants === null ? (
        <p role="status">Loading your review grants…</p>
      ) : grants.length === 0 ? (
        <p>No active source-review grants.</p>
      ) : (
        grants.map((grant) => (
          <GrantReview key={grant.grant_id} grant={grant} />
        ))
      )}
    </main>
  );
}
