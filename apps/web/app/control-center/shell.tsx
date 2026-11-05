"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { createContext, useContext, useEffect, useState } from "react";
import { canViewRoute, workspaceLanding } from "../../lib/access";
import { useAuth } from "../../lib/auth";
import { controlApi, type PlatformOverview } from "./control-api";
import css from "./control-center.module.css";

export const controlLinks = [
  ["Overview", "/control-center", "platform.control_center.read"],
  [
    "Organisations",
    "/control-center/organisations",
    "platform.organisation.read",
  ],
  [
    "Standards & packs",
    "/control-center/standards",
    "standard_pack.read|standards.authority.read",
  ],
  [
    "Source Registry",
    "/control-center/source-registry",
    "standards.source.read|standards.document.read",
  ],
  ["Question Bank", "/control-center/question-bank", "question_bank.read"],
  ["Exam governance", "/control-center/exams", "exam.read"],
  ["Audit evidence", "/control-center/audit", "platform.audit.read"],
  ["AI operations", "/control-center/ai-operations", "ai.read"],
  ["Evaluation", "/control-center/evaluation", "standards.evaluation.read"],
  [
    "Feedback & Improvements",
    "/control-center/feedback",
    "standards.evaluation.read",
  ],
  ["System health", "/control-center/system", "platform.system.read"],
  ["Certification", "/control-center/certification", "certification.read"],
] as const;

const OverviewContext = createContext<{
  overview: PlatformOverview | null;
  error: string | null;
  refresh: () => void;
}>({ overview: null, error: null, refresh: () => undefined });

export const usePlatformOverview = () => useContext(OverviewContext);

export function ControlCentreShell({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, membership, loading, signOut, error: sessionError } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [overview, setOverview] = useState<PlatformOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [signingOut, setSigningOut] = useState(false);
  const allowed = Boolean(
    user &&
      canViewRoute("/control-center", membership, user.platform_permissions),
  );
  useEffect(() => {
    let cancelled = false;
    setOverview(null);
    setError(null);
    if (!allowed) return;
    void controlApi
      .overview()
      .then((value) => {
        if (!cancelled) setOverview(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error
              ? cause.message
              : "Platform overview is unavailable.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [allowed, reload, user?.id]);

  if (loading)
    return (
      <main className={css.workspace}>
        <p role="status">Loading secure platform access…</p>
      </main>
    );
  if (!user)
    return (
      <main className={css.workspace}>
        <p role="status">Opening sign in…</p>
        <Link href="/login">Sign in</Link>
      </main>
    );
  if (!allowed)
    return (
      <main className={css.workspace}>
        <section className={css.main} role="alert">
          <p className={css.eyebrow}>Platform governance</p>
          <h1>Access denied</h1>
          <p>
            The Control Centre requires explicit platform permissions.
            Institution membership does not grant platform access.
          </p>
          <Link href={workspaceLanding(user)}>Return to your workspace</Link>
        </section>
      </main>
    );
  return (
    <OverviewContext.Provider
      value={{
        overview,
        error,
        refresh: () => setReload((value) => value + 1),
      }}
    >
      <div className={css.workspace}>
        <a className={css.skip} href="#platform-content">
          Skip to platform content
        </a>
        <header className={css.header}>
          <div>
            <p className={css.eyebrow}>PrüfAI · Platform governance</p>
            <p className={css.brand}>Control Centre</p>
          </div>
          <div className={css.operator}>
            <span className={css.environment}>
              {overview?.environment ?? "Environment not verified"}
            </span>
            <span>{user.display_name ?? user.email}</span>
            <button
              className={css.logout}
              disabled={signingOut}
              onClick={() => {
                setSigningOut(true);
                void signOut()
                  .then(() => router.replace("/login"))
                  .catch(() => setSigningOut(false));
              }}
            >
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </header>
        <div className={css.body}>
          <nav className={css.navigation} aria-label="Platform navigation">
            {controlLinks
              .filter(([, , permission]) =>
                permission
                  .split("|")
                  .some((key) => user.platform_permissions.includes(key)),
              )
              .map(([label, href]) => (
                <Link
                  href={href}
                  key={href}
                  aria-current={pathname === href ? "page" : undefined}
                >
                  {label}
                </Link>
              ))}
          </nav>
          <main id="platform-content" className={css.main} key={user.id}>
            {sessionError && (
              <p className={css.error} role="alert">
                {sessionError}
              </p>
            )}
            {error && pathname !== "/control-center" && (
              <div className={css.error} role="alert">
                <p>Platform environment metadata is unavailable. {error}</p>
                <button
                  className={css.secondary}
                  onClick={() => setReload((value) => value + 1)}
                >
                  Retry platform metadata
                </button>
              </div>
            )}
            {children}
          </main>
        </div>
      </div>
    </OverviewContext.Provider>
  );
}
