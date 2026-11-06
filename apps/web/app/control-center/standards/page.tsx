"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "../../../lib/auth";
import css from "../control-center.module.css";
import { MetadataBrowser } from "../metadata";
import {
  packPath,
  standardsRequest,
  type FoundationDraft,
} from "../standards-api";

const referencePattern = "[0-9a-fA-F-]{36}";

function DraftWorkbench({ inspect }: { inspect: string }) {
  const { user } = useAuth();
  const canRead = Boolean(
    user?.platform_permissions.includes("standard_pack.read"),
  );
  const canManage = Boolean(
    user?.platform_permissions.includes("standard_pack.manage"),
  );
  const [versionId, setVersionId] = useState("");
  const [draft, setDraft] = useState<FoundationDraft | null>(null);
  const [working, setWorking] = useState(false);
  const [validation, setValidation] = useState("");
  const [error, setError] = useState("");

  async function work(action: () => Promise<void>) {
    setWorking(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Pack operation failed.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function load(id: string) {
    const item = await standardsRequest<FoundationDraft>(
      `${packPath}/${encodeURIComponent(id)}`,
    );
    setDraft(item);
    setVersionId(item.id);
  }

  useEffect(() => {
    if (!inspect || !canRead) return;
    let current = true;
    setWorking(true);
    setVersionId(inspect);
    setDraft(null);
    setError("");
    setValidation("");
    void standardsRequest<FoundationDraft>(
      `${packPath}/${encodeURIComponent(inspect)}`,
    )
      .then((item) => {
        if (current) setDraft(item);
      })
      .catch((cause: unknown) => {
        if (current)
          setError(
            cause instanceof Error
              ? cause.message
              : "Pack version could not be loaded.",
          );
      })
      .finally(() => {
        if (current) setWorking(false);
      });
    return () => {
      current = false;
    };
  }, [canRead, inspect]);

  return (
    <section aria-label="B1 Standard Pack operations">
      <h2>CEFR/GER B1 foundation draft</h2>
      <p className={css.notice}>
        PrüfAI alignment draft. Synthetic practice content. Not official
        provider material.
      </p>
      {!canRead ? (
        <p role="alert" className={css.error}>
          Your platform permissions do not allow pack version inspection.
        </p>
      ) : (
        <>
          {canManage && (
            <form
              className={css.filters}
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void work(async () => {
                  setValidation("");
                  const created = await standardsRequest<FoundationDraft>(
                    "/api/v1/platform/standard-packs/cefr-b1-foundation",
                    "POST",
                    {
                      source_version_id: String(
                        data.get("source_version_id"),
                      ).trim(),
                      expected_metadata_checksum: String(
                        data.get("metadata_checksum"),
                      ).trim(),
                    },
                  );
                  setDraft(created);
                  setVersionId(created.id);
                });
              }}
            >
              <label>
                Reviewed CEFR source version
                <input
                  name="source_version_id"
                  required
                  pattern={referencePattern}
                />
              </label>
              <label>
                Reviewed source metadata checksum
                <input
                  name="metadata_checksum"
                  required
                  pattern="[a-f0-9]{64}"
                />
              </label>
              <button className={css.button} disabled={working}>
                Create or inspect B1 draft
              </button>
            </form>
          )}
          <form
            className={css.filters}
            onSubmit={(event) => {
              event.preventDefault();
              void work(async () => {
                setDraft(null);
                setValidation("");
                await load(versionId.trim());
              });
            }}
          >
            <label>
              Pack version reference
              <input
                value={versionId}
                onChange={(event) => setVersionId(event.target.value)}
                required
                pattern={referencePattern}
              />
            </label>
            <button className={css.secondary} disabled={working}>
              Inspect pack version
            </button>
          </form>
        </>
      )}
      {working && <p role="status">Loading exact pack evidence…</p>}
      {error && (
        <p role="alert" className={css.error}>
          {error}
        </p>
      )}
      {canRead && draft && (
        <article className={css.card} aria-label="Exact pack version detail">
          <h3>{draft.version_label}</h3>
          <dl>
            <dt>Pack version reference</dt>
            <dd>{draft.id}</dd>
            <dt>Pack lifecycle</dt>
            <dd>{draft.status}</dd>
            <dt>Assessment scope</dt>
            <dd>
              {draft.assessment_family ?? "—"} · {draft.language ?? "—"} ·{" "}
              {draft.supported_levels?.join(", ") ?? "—"} ·{" "}
              {draft.allowed_classifications?.join(", ") ?? "—"}
            </dd>
            <dt>Pack checksum</dt>
            <dd>
              {draft.checksum ?? draft.configuration_checksum ?? "Unavailable"}
            </dd>
            <dt>Validation status</dt>
            <dd>
              {validation || draft.validation_status || "No validation shown"}
            </dd>
          </dl>
          {draft.foundation && canManage && (
            <button
              type="button"
              className={css.secondary}
              disabled={working}
              onClick={() =>
                void work(async () => {
                  const result = await standardsRequest<{ status: string }>(
                    `${packPath}/${draft.id}/validate`,
                    "POST",
                  );
                  await load(draft.id);
                  setValidation(result.status);
                })
              }
            >
              Validate exact draft
            </button>
          )}
          {draft.publication_blockers &&
            draft.publication_blockers.length > 0 && (
              <section aria-label="Publication blockers">
                <h4>Publication blocked</h4>
                <ul>
                  {draft.publication_blockers.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </section>
            )}
          {draft.source_bindings && (
            <section aria-label="Exact source bindings">
              <h4>Exact source bindings</h4>
              {!draft.source_bindings.length ? (
                <p>No source bindings on this version.</p>
              ) : (
                <ul>
                  {draft.source_bindings.map((binding) => (
                    <li key={binding.id}>
                      {binding.binding_type} · {binding.purpose} ·{" "}
                      {binding.publisher} · {binding.version_label} ·{" "}
                      {binding.content_mode} · Rights{" "}
                      {binding.legal_review_status} · Source version{" "}
                      {binding.document_version_id} ·{" "}
                      {binding.source_metadata_checksum ??
                        binding.document_checksum ??
                        "No checksum recorded"}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
          {draft.constructs && draft.constructs.length > 0 && (
            <section aria-label="Mapped assessment constructs">
              <h4>Mapped assessment constructs</h4>
              <ul>
                {draft.constructs.map((construct, index) => (
                  <li key={String(construct.code ?? index)}>
                    {[
                      construct.skill,
                      construct.task_code,
                      construct.construct,
                      construct.title ?? construct.name,
                      construct.task_specification_id,
                      construct.review_status,
                    ]
                      .filter((item) => typeof item === "string")
                      .join(" · ")}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </article>
      )}
    </section>
  );
}

export default function StandardsPage() {
  const [inspect, setInspect] = useState("");
  return (
    <MetadataBrowser
      section="standards"
      onInspect={setInspect}
      workbench={
        <>
          <p>
            <Link href="/control-center/standards/dtz">
              DTZ A2–B1 blueprint
            </Link>
          </p>
          <DraftWorkbench inspect={inspect} />
        </>
      }
    />
  );
}
