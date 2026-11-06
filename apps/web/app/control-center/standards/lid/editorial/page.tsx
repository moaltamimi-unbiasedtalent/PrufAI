"use client";

import Link from "next/link";
import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth } from "../../../../../lib/auth";
import css from "../../../control-center.module.css";
import { sourceLink } from "../../dtz/blueprint-detail";
import type { LidBlueprintSummary } from "../lid-api";
import {
  editorialApi,
  evidenceLabel,
  type EditorialCollection,
  type EditorialCollectionSummary,
  type EditorialItem,
  type EditorialPreview,
  type EditorialReceipt,
  type ItemRevision,
} from "./editorial-api";
import { EditorialItemDetail, type ItemDecision } from "./item-detail";

const countLabels = [
  ["drafted", "Drafted items"],
  ["fact_check_pending", "Fact checks pending"],
  ["expert_review_pending", "Expert reviews pending"],
  ["rights_pending", "Original content rights pending"],
  ["approved", "Approved items"],
  ["rejected", "Rejected items"],
  ["withdrawn", "Withdrawn items"],
  ["published", "Published items"],
  ["general_pool_eligible", "General pool eligible"],
  ["berlin_pool_eligible", "Berlin pool eligible"],
  ["test_review_decisions", "TEST ONLY review decisions"],
] as const;

function message(cause: unknown) {
  return cause instanceof Error
    ? cause.message
    : "Editorial evidence is unavailable.";
}

function PackEvidence({
  collection,
  busy,
  act,
}: {
  collection: EditorialCollection;
  busy: boolean;
  act: (action: "approve" | "publish") => void;
}) {
  const general = collection.items.filter(
    (item) => item.scope === "GENERAL_DE",
  ).length;
  const berlin = collection.items.filter(
    (item) => item.jurisdiction === "DE-BE",
  ).length;
  return (
    <section className={css.card} aria-label="Berlin editorial collection">
      <h2>Berlin editorial collection</h2>
      <p>
        {collection.code} · Pack {collection.pack.status}
      </p>
      <dl className={css.health}>
        <dt>Drafted general</dt>
        <dd>{general}</dd>
        <dt>Drafted Berlin</dt>
        <dd>{berlin}</dd>
        {countLabels.map(([key, label]) => (
          <Fragment key={key}>
            <dt>{label}</dt>
            <dd>{collection.counts[key]}</dd>
          </Fragment>
        ))}
      </dl>
      <details>
        <summary>Exact collection and pack references</summary>
        <dl className={css.health}>
          <dt>Collection reference</dt>
          <dd>{collection.id}</dd>
          <dt>Collection checksum</dt>
          <dd>{collection.checksum}</dd>
          <dt>LiD blueprint reference</dt>
          <dd>{collection.blueprint_version_id}</dd>
          <dt>Standard Pack version</dt>
          <dd>{collection.pack.id}</dd>
          <dt>Standard Pack checksum</dt>
          <dd>{collection.pack.checksum}</dd>
        </dl>
      </details>
      <section aria-label="Practice publication blockers">
        <h3>Practice publication blockers</h3>
        {collection.blockers.length ? (
          <ul>
            {collection.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        ) : (
          <p>No blockers recorded for this exact collection.</p>
        )}
        {collection.allowed_actions.approve && (
          <button
            className={css.secondary}
            disabled={busy}
            type="button"
            onClick={() => act("approve")}
          >
            Approve eligible practice pack
          </button>
        )}
        {collection.allowed_actions.publish && (
          <button
            className={css.button}
            disabled={busy}
            type="button"
            onClick={() => act("publish")}
          >
            Publish approved practice pack
          </button>
        )}
      </section>
    </section>
  );
}

function PreviewEvidence({ preview }: { preview: EditorialPreview }) {
  const general = preview.items.filter(
    (item) => item.scope === "GENERAL_DE",
  ).length;
  const berlin = preview.items.filter(
    (item) => item.jurisdiction === "DE-BE",
  ).length;
  return (
    <section className={css.card} aria-label="Pinned Berlin editorial preview">
      <h3>Pinned Berlin editorial preview</h3>
      <p className={css.notice}>
        Editorial preview · {general} general + {berlin} Berlin ·{" "}
        {preview.duration_seconds / 60} minutes · Review access only
      </p>
      <dl className={css.health}>
        <dt>Mode</dt>
        <dd>{preview.mode}</dd>
        <dt>Collection checksum</dt>
        <dd>{preview.collection_checksum}</dd>
        <dt>Preview checksum</dt>
        <dd>{preview.checksum}</dd>
        <dt>Blueprint reference</dt>
        <dd>{preview.blueprint_version_id}</dd>
        <dt>Practice pack version</dt>
        <dd>{preview.pack_version_id}</dd>
        <dt>Jurisdiction</dt>
        <dd>{preview.bundesland}</dd>
      </dl>
      <ol aria-label="Pinned editorial preview slots">
        {preview.items.map((item) => (
          <li key={item.item_version_id}>
            <details>
              <summary>
                {item.slot} ·{" "}
                {item.scope === "GENERAL_DE" ? "General" : item.jurisdiction} ·{" "}
                {item.prompt}
              </summary>
              <p>
                {item.item_version_id} · {item.provenance_checksum} ·{" "}
                {evidenceLabel(item.maturity)}
              </p>
              <ol aria-label="Pinned answer options">
                {item.options.map(({ option_id, display_text }) => (
                  <li key={option_id}>
                    {display_text}
                    {option_id === item.correct_option_id && (
                      <>
                        {" · "}
                        <strong>Proposed answer</strong>
                      </>
                    )}
                  </li>
                ))}
              </ol>
              <p>{item.rationale}</p>
              <p>
                {sourceLink(item.claim.reference_url, item.source.title)} ·{" "}
                {item.claim.locator}
              </p>
              {item.blockers.length > 0 && (
                <ul>
                  {item.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              )}
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function LidEditorialPage() {
  const { user } = useAuth();
  const platform = user?.platform_permissions ?? [];
  const hasPlatform = (permission: string) =>
    platform.includes(permission) ||
    platform.includes(`platform.${permission}`);
  const activeMemberships =
    user?.memberships.filter(({ status }) => status === "ACTIVE") ?? [];
  const canReadPlatform = hasPlatform("question_bank.read");
  const memberships = activeMemberships.filter(
    ({ permissions }) =>
      canReadPlatform || permissions.includes("question_bank.read"),
  );
  const [organisationChoice, setOrganisationChoice] = useState("");
  const membership =
    memberships.find(
      ({ organisation_id }) => organisation_id === organisationChoice,
    ) ?? memberships[0];
  const organisation = membership?.organisation_id ?? "";
  const canCreate = Boolean(
    membership &&
      ["question_bank.manage", "standard_pack.manage"].every(
        (permission) =>
          hasPlatform(permission) ||
          membership.permissions.includes(permission),
      ) &&
      [
        "standard_pack.read",
        "standards.document.read",
        "standards.authority.manage",
        "standards.source.manage",
        "standards.document.manage",
      ].every(hasPlatform),
  );
  const [blueprints, setBlueprints] = useState<LidBlueprintSummary[]>([]);
  const [collections, setCollections] = useState<
    EditorialCollectionSummary[] | null
  >(null);
  const [blueprintChoice, setBlueprintChoice] = useState("");
  const blueprint =
    blueprints.find(({ id }) => id === blueprintChoice) ?? blueprints[0];
  const [collection, setCollection] = useState<EditorialCollection | null>(
    null,
  );
  const [item, setItem] = useState<EditorialItem | null>(null);
  const [preview, setPreview] = useState<EditorialPreview | null>(null);
  const [receipt, setReceipt] = useState<EditorialReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const operations = useRef(new Map<string, string>());

  useEffect(() => {
    let current = true;
    setCollections(null);
    setCollection(null);
    setItem(null);
    setPreview(null);
    setReceipt(null);
    setError("");
    setBlueprints([]);
    setBlueprintChoice("");
    operations.current.clear();
    if (!organisation) return;
    void Promise.all([
      editorialApi.collections(organisation),
      canCreate ? editorialApi.blueprints() : Promise.resolve([]),
    ])
      .then(([known, versions]) => {
        if (current) {
          setCollections(known);
          setBlueprints(versions);
        }
      })
      .catch((cause: unknown) => {
        if (current) setError(message(cause));
      });
    return () => {
      current = false;
    };
  }, [canCreate, organisation, user?.id]);

  function operation(key: string) {
    const existing = operations.current.get(key);
    if (existing) return existing;
    const id = crypto.randomUUID();
    operations.current.set(key, id);
    return id;
  }

  async function work(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause: unknown) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  function selectCollection(id: string) {
    setCollection(null);
    setItem(null);
    setPreview(null);
    setReceipt(null);
    if (id)
      void work(async () => setCollection(await editorialApi.collection(id)));
  }

  function inspect(version: string) {
    if (!collection) return;
    setItem(null);
    setReceipt(null);
    if (version)
      void work(async () =>
        setItem(await editorialApi.item(collection.id, version)),
      );
  }

  function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!blueprint) return;
    void work(async () => {
      const next = await editorialApi.create(
        organisation,
        blueprint,
        operation(`create:${organisation}:${blueprint.checksum}`),
      );
      setCollection(next);
      setItem(null);
      setPreview(null);
      setReceipt(null);
      setCollections((previous) => [
        next,
        ...(previous ?? []).filter(({ id }) => id !== next.id),
      ]);
    });
  }

  function actOnPack(action: "approve" | "publish") {
    if (!collection) return;
    void work(async () => {
      setCollection(
        await editorialApi.packDecision(
          collection,
          action,
          operation(`${action}:${collection.id}:${collection.checksum}`),
        ),
      );
      setPreview(null);
    });
  }

  function review(decision?: ItemDecision) {
    if (!collection || !item) return;
    void work(async () => {
      const pin = {
        expected_checksum: item.checksum,
        operation_id: operation(
          `review:${item.item_version_id}:${item.checksum}:${JSON.stringify(decision ?? null)}`,
        ),
      };
      const recorded = decision
        ? await editorialApi.decision(collection.id, item.item_version_id, {
            ...pin,
            ...decision,
          })
        : await editorialApi.submit(collection.id, item.item_version_id, pin);
      setReceipt(recorded);
      setPreview(null);
      const [currentCollection, currentItem] = await Promise.all([
        editorialApi.collection(collection.id),
        editorialApi.item(collection.id, item.item_version_id),
      ]);
      setCollection(currentCollection);
      setItem(currentItem);
    });
  }

  function revise(revision: ItemRevision) {
    if (!collection || !item) return;
    void work(async () => {
      const successor = await editorialApi.successor(
        collection.id,
        item.item_version_id,
        {
          expected_checksum: item.provenance_checksum,
          operation_id: operation(
            `revise:${item.item_version_id}:${item.provenance_checksum}:${JSON.stringify(revision)}`,
          ),
          ...revision,
        },
      );
      setItem(successor);
      setPreview(null);
      setReceipt(null);
      setCollection(await editorialApi.collection(collection.id));
    });
  }

  return (
    <>
      <p>
        <Link href="/control-center/standards/lid">
          Leben in Deutschland blueprint
        </Link>
        {" · "}
        <Link href="/control-center/question-bank">Question Bank</Link>
      </p>
      <h1>Original Berlin LiD editorial set</h1>
      <p className={css.notice}>
        Original practice drafts for independent review · NOT OFFICIAL · NOT
        CERTIFIED
      </p>
      {!organisation ? (
        <p role="alert" className={css.error}>
          {activeMemberships.length
            ? "Question Bank read permission is required for the editorial workbench."
            : "An active organisation membership is required to open the editorial workbench."}
        </p>
      ) : (
        <>
          <div className={css.filters}>
            <label>
              Editorial organisation
              <select
                value={organisation}
                onChange={(event) => setOrganisationChoice(event.target.value)}
                disabled={busy}
              >
                {memberships.map(({ organisation_id, organisation_name }) => (
                  <option key={organisation_id} value={organisation_id}>
                    {organisation_name}
                  </option>
                ))}
              </select>
            </label>
            {collections !== null && (
              <label>
                Editorial collection
                <select
                  value={collection?.id ?? ""}
                  onChange={(event) => selectCollection(event.target.value)}
                  disabled={busy}
                >
                  <option value="">Select a collection</option>
                  {collections.map(({ id, code }) => (
                    <option key={id} value={id}>
                      {code}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {collections?.length === 0 && (
            <p>No editorial collection exists for this organisation.</p>
          )}
          {canCreate && collections?.length === 0 && (
            <form
              className={css.filters}
              aria-label="Create Berlin editorial draft"
              onSubmit={create}
            >
              <label>
                Exact LiD blueprint
                <select
                  value={blueprint?.id ?? ""}
                  onChange={(event) => setBlueprintChoice(event.target.value)}
                  disabled={busy || !blueprints.length}
                >
                  {!blueprints.length && (
                    <option value="">
                      No governed LiD blueprint available
                    </option>
                  )}
                  {blueprints.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.version_label} · {entry.checksum}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className={css.button}
                type="submit"
                disabled={busy || !blueprint}
              >
                Create Berlin editorial draft
              </button>
            </form>
          )}
          {busy || (collections === null && !error) ? (
            <p role="status">Loading editorial evidence…</p>
          ) : null}
          {error && (
            <p role="alert" className={css.error}>
              {error}
            </p>
          )}
          {collection && (
            <>
              <PackEvidence
                collection={collection}
                busy={busy}
                act={actOnPack}
              />
              <div className={css.filters}>
                <label>
                  Exact editorial item
                  <select
                    value={item?.item_version_id ?? ""}
                    onChange={(event) => inspect(event.target.value)}
                    disabled={busy}
                  >
                    <option value="">Select an exact item</option>
                    {item &&
                      !collection.items.some(
                        ({ item_version_id }) =>
                          item_version_id === item.item_version_id,
                      ) && (
                        <option value={item.item_version_id}>
                          {item.code} · {item.version_label} · History
                        </option>
                      )}
                    {collection.items.map((row) => (
                      <option
                        key={row.item_version_id}
                        value={row.item_version_id}
                      >
                        {row.code} ·{" "}
                        {row.scope === "GENERAL_DE"
                          ? "General"
                          : row.jurisdiction}{" "}
                        · {evidenceLabel(row.maturity)}
                      </option>
                    ))}
                  </select>
                </label>
                {collection.allowed_actions.preview && (
                  <button
                    className={css.secondary}
                    disabled={busy}
                    type="button"
                    onClick={() =>
                      void work(async () =>
                        setPreview(await editorialApi.preview(collection)),
                      )
                    }
                  >
                    Open pinned Berlin editorial preview
                  </button>
                )}
              </div>
              {receipt && (
                <p role="status" aria-label="Editorial review receipt">
                  {evidenceLabel(receipt.evidence_class)} · Recorded review
                  receipt {receipt.id}
                </p>
              )}
              {item && (
                <EditorialItemDetail
                  item={item}
                  busy={busy}
                  onSubmit={() => review()}
                  onDecision={review}
                  onRevise={revise}
                  onInspect={inspect}
                />
              )}
              {preview && <PreviewEvidence preview={preview} />}
            </>
          )}
        </>
      )}
    </>
  );
}
