"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "../../../../lib/auth";
import css from "../../control-center.module.css";
import {
  sourcePath,
  standardsRequest,
  type SourceVersion,
} from "../../standards-api";
import { BlueprintDetail, sourceLink } from "./blueprint-detail";
import {
  dtzPath,
  effectiveDate,
  type DtzBlueprint,
  type DtzSource,
  type DtzSummary,
} from "./dtz-api";

const sourceList = `${dtzPath}/sources`;
const blueprintList = `${dtzPath}/blueprints`;
function SourceDetail({
  source,
  exact,
  canRegister,
  busy,
  register,
  inspect,
}: {
  source: DtzSource;
  exact: SourceVersion | null;
  canRegister: boolean;
  busy: boolean;
  register: () => void;
  inspect: () => void;
}) {
  const version = source.registered;
  return (
    <article className={css.card} aria-label="DTZ source metadata">
      <h3>{source.title}</h3>
      <dl className={css.health}>
        <dt>Publisher</dt>
        <dd>{source.publisher}</dd>
        <dt>Edition</dt>
        <dd>{source.version_label}</dd>
        <dt>Publication</dt>
        <dd>{source.publication_label}</dd>
        <dt>Verified on</dt>
        <dd>{source.verified_on}</dd>
        <dt>Effective from</dt>
        <dd>{effectiveDate(source.effective_from)}</dd>
        <dt>Source</dt>
        <dd>{sourceLink(source.edition_url ?? source.source_url)}</dd>
        <dt>Rights holder</dt>
        <dd>{source.rights_holder}</dd>
        <dt>Scope</dt>
        <dd>{source.rights_note}</dd>
        <dt>Exact source version</dt>
        <dd>{version?.id ?? "Unregistered"}</dd>
        <dt>Metadata checksum</dt>
        <dd>{version?.metadata_checksum ?? "Not recorded"}</dd>
        <dt>Bibliographic rights review</dt>
        <dd>{version?.legal_review_status ?? "Pending registration"}</dd>
      </dl>
      {canRegister && !version && (
        <button
          type="button"
          className={css.secondary}
          disabled={busy}
          onClick={register}
        >
          Register DTZ source metadata
        </button>
      )}
      {version && (
        <>
          <button
            type="button"
            className={css.secondary}
            disabled={busy}
            onClick={inspect}
          >
            Inspect exact DTZ source version
          </button>
          {!version.reviewed &&
            version.legal_review_status === "NOT_REVIEWED" && (
              <p>
                Record an independent bibliographic rights decision in the{" "}
                <Link href="/control-center/source-registry">
                  Source Registry
                </Link>
                .
              </p>
            )}
          {!version.reviewed &&
            version.legal_review_status !== "NOT_REVIEWED" && (
              <p>
                This recorded decision remains in history. The exact version is
                unavailable for this preview.
              </p>
            )}
        </>
      )}
      {exact?.id === version?.id && exact && (
        <section aria-label="Exact DTZ source rights">
          <h4>Rights and permitted use</h4>
          <dl className={css.health}>
            <dt>Content mode</dt>
            <dd>{exact.content_mode}</dd>
            <dt>Decision</dt>
            <dd>{exact.legal_review_status}</dd>
            <dt>Reviewer reference</dt>
            <dd>{exact.legal_reviewed_by_user_id ?? "Not recorded"}</dd>
            <dt>Decision checksum</dt>
            <dd>{exact.decision_checksum ?? "Not recorded"}</dd>
            <dt>Permitted use</dt>
            <dd>{exact.usage_rights ?? "Not recorded"}</dd>
            <dt>Derivative use</dt>
            <dd>{exact.derivative_use_allowed ? "Allowed" : "Restricted"}</dd>
            <dt>Redistribution</dt>
            <dd>{exact.redistribution_allowed ? "Allowed" : "Restricted"}</dd>
            <dt>Ingestion</dt>
            <dd>{exact.ingestion_allowed ? "Allowed" : "Restricted"}</dd>
            <dt>Retention</dt>
            <dd>{exact.retention_policy ?? "Not recorded"}</dd>
          </dl>
        </section>
      )}
    </article>
  );
}

export default function DtzStandardsPage() {
  const { user } = useAuth();
  const permissions = user?.platform_permissions ?? [];
  const canRead = [
    "standard_pack.read",
    "standards.source.read",
    "standards.document.read",
  ].every((permission) => permissions.includes(permission));
  const canManage =
    permissions.includes("standard_pack.manage") &&
    permissions.includes("rules.manage");
  const canPublish = permissions.includes("standard_pack.publish");
  const canPreview = permissions.includes("rules.evaluate");
  const canRegister = [
    "standards.authority.manage",
    "standards.source.manage",
    "standards.document.manage",
  ].every((permission) => permissions.includes(permission));
  const [sources, setSources] = useState<DtzSource[] | null>(null);
  const [versions, setVersions] = useState<DtzSummary[] | null>(null);
  const [sourceKey, setSourceKey] = useState("");
  const [sourceDetail, setSourceDetail] = useState<SourceVersion | null>(null);
  const [draft, setDraft] = useState<DtzBlueprint | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    if (!canRead) return;
    let active = true;
    setWorking(true);
    setError("");
    void Promise.all([
      standardsRequest<DtzSource[]>(sourceList),
      standardsRequest<DtzSummary[]>(blueprintList),
    ])
      .then(([registered, history]) => {
        if (!active) return;
        setSources(registered);
        setVersions(history);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(
            cause instanceof Error
              ? cause.message
              : "DTZ workbench could not be loaded.",
          );
      })
      .finally(() => {
        if (active) setWorking(false);
      });
    return () => {
      active = false;
    };
  }, [canRead, refresh]);

  async function work(action: () => Promise<void>) {
    setWorking(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "DTZ operation failed.",
      );
    } finally {
      setWorking(false);
    }
  }

  const selected = sources?.find((source) => source.key === sourceKey);
  const pins = Object.fromEntries(
    (sources ?? []).flatMap(({ key, registered }) =>
      registered?.reviewed
        ? [
            [
              key,
              {
                version_id: registered.id,
                metadata_checksum: registered.metadata_checksum,
              },
            ],
          ]
        : [],
    ),
  );
  const readyCount = Object.keys(pins).length;
  const canCreate = Boolean(sources?.length && readyCount === sources.length);

  return (
    <>
      <p>
        <Link href="/control-center/standards">
          Back to Standards &amp; packs
        </Link>
      </p>
      <h1>DTZ A2–B1 blueprint</h1>
      <p className={css.notice}>
        DTZ-informed PRACTICE draft. Official assessment equivalence is not
        established.
      </p>
      {!canRead ? (
        <p role="alert" className={css.error}>
          Your permissions do not allow DTZ blueprint inspection.
        </p>
      ) : (
        <>
          {error && (
            <p role="alert" className={css.error}>
              {error}
            </p>
          )}
          {working && <p role="status">Loading governed DTZ evidence…</p>}
          <section aria-labelledby="dtz-sources-heading">
            <h2 id="dtz-sources-heading">DTZ source register</h2>
            <div className={css.filters}>
              <label>
                DTZ source
                <select
                  value={sourceKey}
                  disabled={working || !sources?.length}
                  onChange={(event) => {
                    setSourceKey(event.target.value);
                    setSourceDetail(null);
                  }}
                >
                  <option value="">Select a source</option>
                  {sources?.map((source) => (
                    <option key={source.key} value={source.key}>
                      {source.title}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className={css.secondary}
                disabled={working}
                onClick={() => {
                  setSourceDetail(null);
                  setDraft(null);
                  setRefresh((value) => value + 1);
                }}
              >
                Refresh exact sources and versions
              </button>
            </div>
            {selected && (
              <SourceDetail
                source={selected}
                exact={sourceDetail}
                canRegister={canRegister}
                busy={working}
                register={() =>
                  void work(async () => {
                    const registered = await standardsRequest<DtzSource>(
                      `${sourceList}/${encodeURIComponent(selected.key)}/register`,
                      "POST",
                    );
                    setSources(
                      (list) =>
                        list?.map((source) =>
                          source.key === registered.key ? registered : source,
                        ) ?? null,
                    );
                  })
                }
                inspect={() =>
                  void work(async () => {
                    if (!selected.registered) return;
                    setSourceDetail(
                      await standardsRequest<SourceVersion>(
                        `${sourcePath}/versions/${selected.registered.id}`,
                      ),
                    );
                  })
                }
              />
            )}
          </section>

          <section aria-labelledby="dtz-versions-heading">
            <h2 id="dtz-versions-heading">DTZ blueprint version history</h2>
            {sources && (
              <p>
                {readyCount} of {sources.length} sources have reviewed, exact
                metadata versions.
              </p>
            )}
            {canManage && (
              <button
                type="button"
                className={css.button}
                disabled={working || !canCreate}
                onClick={() =>
                  void work(async () => {
                    const next = await standardsRequest<DtzBlueprint>(
                      blueprintList,
                      "POST",
                      { sources: pins },
                    );
                    setDraft(next);
                    setVersions(
                      await standardsRequest<DtzSummary[]>(blueprintList),
                    );
                  })
                }
              >
                Create or inspect DTZ draft
              </button>
            )}
            {versions?.length === 0 && (
              <p>No DTZ blueprint versions have been created.</p>
            )}
            {Boolean(versions?.length) && (
              <ul>
                {versions?.map((version) => (
                  <li key={version.id}>
                    {version.version_label} · {version.status} ·{" "}
                    <code>{version.checksum}</code>{" "}
                    <button
                      type="button"
                      className={css.secondary}
                      disabled={working}
                      onClick={() =>
                        void work(async () => {
                          setDraft(
                            await standardsRequest<DtzBlueprint>(
                              `${blueprintList}/${version.id}`,
                            ),
                          );
                        })
                      }
                    >
                      Inspect DTZ version {version.version_label}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {draft && (
            <BlueprintDetail
              key={draft.id}
              draft={draft}
              canPublish={canPublish}
              canPreview={canPreview}
            />
          )}
        </>
      )}
    </>
  );
}
