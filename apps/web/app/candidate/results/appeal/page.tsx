"use client";

import Link from "next/link";
import React, { FormEvent, useEffect, useRef, useState } from "react";
import {
  releaseApi,
  type ResultRelease,
} from "../../../institution/releases/release-api";
import { appealApi } from "../../../institution/appeals/appeal-api";
import css from "../../../institution/assessment.module.css";

export default function CandidateAppealPage() {
  const [releases, setReleases] = useState<ResultRelease[] | null>(null);
  const [resultVersionId, setResultVersionId] = useState("");
  const [category, setCategory] = useState("");
  const [grounds, setGrounds] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const operations = useRef<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    setReleases(null);
    setError(null);
    void releaseApi
      .candidateList()
      .then((items) => {
        if (cancelled) return;
        const eligible = items.filter(
          (item) => (item.effective_status ?? item.status) === "RELEASED",
        );
        setReleases(eligible);
        const selected = new URLSearchParams(window.location.search).get(
          "resultVersionId",
        );
        setResultVersionId((current) =>
          selected &&
          eligible.some((item) => item.result_version_id === selected)
            ? selected
            : eligible.some((item) => item.result_version_id === current)
              ? current
              : "",
        );
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Released results could not be loaded.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (
      !resultVersionId ||
      !category.trim() ||
      !grounds.trim() ||
      busy ||
      submitted
    )
      return;
    setBusy(true);
    setError(null);
    const key = `${resultVersionId}:${category.trim()}:${grounds.trim()}`;
    const operation = (operations.current[key] ??= crypto.randomUUID());
    try {
      const appeal = await appealApi.submit(
        resultVersionId,
        category.trim(),
        grounds.trim(),
        operation,
      );
      setSubmitted(appeal.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The appeal could not be confirmed. Retry with the same details.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <p>
        <Link href="/candidate/results">← My released results</Link>
      </p>
      <h1>Appeal a released result</h1>
      <p>
        The appeal pins the exact released result version and checksum. A human
        reviews the request; submitting an appeal does not change your score.
      </p>
      {error && (
        <div className={css.error} role="alert">
          <p>{error}</p>
          <button onClick={() => setReload((value) => value + 1)}>
            Reload released results
          </button>
        </div>
      )}
      {!releases && !error && (
        <p role="status">Loading your eligible released results…</p>
      )}
      {releases && !releases.length && (
        <p>No current released result is available to appeal.</p>
      )}
      {releases && releases.length > 0 && (
        <form
          className={`${css.card} ${css.form}`}
          onSubmit={(event) => void submit(event)}
        >
          <label>
            Released result version
            <select
              required
              value={resultVersionId}
              disabled={busy || Boolean(submitted)}
              onChange={(event) => setResultVersionId(event.target.value)}
            >
              <option value="">Select your released result</option>
              {releases.map((release) => (
                <option key={release.id} value={release.result_version_id}>
                  {release.classification} · {release.result_version_id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Category
            <input
              required
              maxLength={80}
              value={category}
              disabled={busy || Boolean(submitted)}
              onChange={(event) => setCategory(event.target.value)}
            />
          </label>
          <label>
            Grounds
            <textarea
              required
              maxLength={10000}
              rows={6}
              value={grounds}
              disabled={busy || Boolean(submitted)}
              onChange={(event) => setGrounds(event.target.value)}
            />
          </label>
          <button disabled={busy || Boolean(submitted) || !resultVersionId}>
            {submitted
              ? "Appeal submitted"
              : busy
                ? "Submitting appeal…"
                : "Submit appeal"}
          </button>
        </form>
      )}
      {submitted && (
        <p className={css.notice} role="status">
          Appeal submitted.{" "}
          <Link href={`/candidate/results/appeals/${submitted}`}>
            Inspect your appeal and decision →
          </Link>
        </p>
      )}
      <p>
        <Link href="/candidate/results/appeals">My appeal history →</Link>
      </p>
    </section>
  );
}
