"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  clearAccountOutbox,
  lockActiveCandidateAccount,
} from "../app/candidate/outbox";
import {
  canViewRoute,
  initialMembership,
  workspaceLanding,
  type DemoUser,
  type Membership,
  workspaceLinks,
} from "./access";
import {
  authFetch,
  authenticatedFetch,
  csrfToken,
  onSessionExpired,
  refreshAccessToken,
  setOrganisationContext,
  setSessionToken,
  withSessionLock,
} from "./api";

export type { DemoUser } from "./access";

type AuthContextValue = {
  user: DemoUser | null;
  loading: boolean;
  organisationId: string | null;
  membership: Membership | undefined;
  permissions: string[];
  demoMode: boolean;
  error: string | null;
  signIn: (email: string, password: string) => Promise<string>;
  signOut: () => Promise<void>;
  selectOrganisation: (id: string) => Promise<void>;
};
const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  organisationId: null,
  membership: undefined,
  permissions: [],
  demoMode: false,
  error: null,
  signIn: async () => "/",
  signOut: async () => undefined,
  selectOrganisation: async () => undefined,
});

export function AuthProvider({
  children,
  demoMode = false,
}: {
  children: React.ReactNode;
  demoMode?: boolean;
}) {
  const [user, setUser] = useState<DemoUser | null>(null);
  const [organisationId, setOrganisationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const current = useRef<{
    user: DemoUser | null;
    organisationId: string | null;
  }>({ user: null, organisationId: null });
  const purgeCurrent = useCallback(async () => {
    const previous = current.current;
    lockActiveCandidateAccount(null);
    if (previous.user && previous.organisationId)
      await clearAccountOutbox(previous.user.id, previous.organisationId);
  }, []);
  const adopt = useCallback(
    (profile: DemoUser) => {
      const chosen = initialMembership(profile, demoMode);
      const id = chosen?.organisation_id ?? null;
      setOrganisationContext(id);
      lockActiveCandidateAccount(
        id ? { userId: profile.id, organisationId: id } : null,
      );
      current.current = { user: profile, organisationId: id };
      setUser(profile);
      setOrganisationId(id);
      setError(null);
    },
    [demoMode],
  );
  const forget = useCallback(() => {
    lockActiveCandidateAccount(null);
    setSessionToken(null);
    current.current = { user: null, organisationId: null };
    setUser(null);
    setOrganisationId(null);
  }, []);
  const announce = () => {
    try {
      window.localStorage.setItem("prufai_session_event", crypto.randomUUID());
    } catch {
      /* Storage can be disabled; server revocation and account locks still apply. */
    }
  };
  useEffect(() => {
    let cancelled = false;
    try {
      window.sessionStorage.removeItem("prufai_demo_access_token");
    } catch {
      // Restricted browser storage must not prevent cookie-based sign in.
    }
    onSessionExpired(() => {
      void purgeCurrent()
        .catch(() =>
          setError(
            "Your session expired; offline storage could not be cleared. Close this tab before using a shared device.",
          ),
        )
        .finally(forget);
      setError("Your session expired. Please sign in again.");
    });
    const switched = (event: StorageEvent) => {
      if (event.key === "prufai_session_event") {
        setSessionToken(null);
        void purgeCurrent()
          .catch(() =>
            setError(
              "Account changed; offline storage could not be cleared. Close this tab before using a shared device.",
            ),
          )
          .finally(forget);
        setError("The account changed in another tab. Please sign in again.");
      }
    };
    window.addEventListener("storage", switched);
    void (async () => {
      try {
        if (await refreshAccessToken()) {
          const response = await authenticatedFetch("/api/v1/auth/me");
          if (!response.ok)
            throw new Error("Your session could not be restored.");
          const profile = (await response.json()) as DemoUser;
          if (!cancelled) adopt(profile);
        }
      } catch {
        if (!cancelled)
          setError(
            "The service is unavailable. Sign in again when it is restored.",
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      onSessionExpired(null);
      window.removeEventListener("storage", switched);
    };
  }, [adopt, forget, purgeCurrent]);

  async function signOut() {
    setError(null);
    lockActiveCandidateAccount(null);
    try {
      const csrf = await csrfToken();
      if (!csrf)
        throw new Error(
          "The server session could not be revoked. Retry sign out.",
        );
      const response = await authenticatedFetch("/api/v1/auth/logout", {
        method: "POST",
        headers: { "X-PrufAI-CSRF": csrf },
      });
      if (response.status !== 204)
        throw new Error(
          "The server session could not be confirmed revoked. Retry sign out when the service is available.",
        );
      try {
        await purgeCurrent();
      } catch {
        setError(
          "Signed out on the server. Offline storage could not be cleared; close this tab before using a shared device.",
        );
      } finally {
        forget();
        announce();
      }
    } catch (cause) {
      const previous = current.current;
      if (previous.user && previous.organisationId)
        lockActiveCandidateAccount({
          userId: previous.user.id,
          organisationId: previous.organisationId,
        });
      setError(cause instanceof Error ? cause.message : "Sign out failed.");
      throw cause;
    }
  }
  async function signIn(email: string, password: string) {
    if (current.current.user) await signOut();
    const response = await withSessionLock(() =>
      authFetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      }),
    );
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? "Email or password is incorrect, or sign in is temporarily rate limited."
          : "Sign in is unavailable. Check that the API and database are running.",
      );
    const data = (await response.json()) as { access_token: string };
    announce();
    setSessionToken(data.access_token);
    const me = await authenticatedFetch("/api/v1/auth/me");
    if (!me.ok) {
      forget();
      throw new Error("Your authenticated profile could not be loaded.");
    }
    const profile = (await me.json()) as DemoUser;
    adopt(profile);
    announce();
    return workspaceLanding(profile, initialMembership(profile, demoMode));
  }
  async function selectOrganisation(id: string) {
    if (
      !user?.memberships.some(
        (item) => item.organisation_id === id && item.status === "ACTIVE",
      )
    )
      throw new Error("Active membership is required.");
    await purgeCurrent();
    setOrganisationContext(id);
    setOrganisationId(id);
    lockActiveCandidateAccount({ userId: user.id, organisationId: id });
    current.current = { user, organisationId: id };
  }
  const membership = user?.memberships.find(
    (item) =>
      item.organisation_id === organisationId && item.status === "ACTIVE",
  );
  const value = {
    user,
    loading,
    organisationId,
    membership,
    permissions: membership?.permissions ?? [],
    demoMode,
    error,
    signIn,
    signOut,
    selectOrganisation,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const {
    user,
    loading,
    membership,
    organisationId,
    demoMode,
    error,
    signOut,
    selectOrganisation,
  } = useAuth();
  const platformPath =
    pathname === "/control-center" || pathname.startsWith("/control-center/");
  useEffect(() => {
    if (platformPath && !loading && !user) router.replace("/login");
  }, [loading, platformPath, router, user]);
  if (["/login", "/docs", "/status"].includes(pathname)) return <>{children}</>;
  if (loading)
    return (
      <main style={styles.center}>
        <p role="status">Loading your secure workspace…</p>
      </main>
    );
  if (!user)
    return (
      <main style={styles.center}>
        <section style={styles.card}>
          <p style={styles.kicker}>PrüfAI</p>
          <h1>Sign in to the platform</h1>
          <p>Sign in with your account to open your workspace.</p>
          {error && <p role="alert">{error}</p>}
          <Link style={styles.primary} href="/login">
            Open sign in
          </Link>
        </section>
      </main>
    );
  if (platformPath) return <>{children}</>;
  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <div>
          <strong>PrüfAI</strong>
          {demoMode && <span style={styles.badge}>LOCAL DEMO</span>}
        </div>
        <div style={styles.user}>
          <span>{user.display_name ?? user.email}</span>
          <button
            style={styles.linkButton}
            onClick={() => {
              void signOut()
                .then(() => router.push("/login"))
                .catch(() => undefined);
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      {user.memberships.filter((item) => item.status === "ACTIVE").length >
        1 && (
        <label style={{ display: "block", padding: "0.7rem 2rem" }}>
          Organisation{" "}
          <select
            value={organisationId ?? ""}
            onChange={(event) => void selectOrganisation(event.target.value)}
          >
            {user.memberships
              .filter((item) => item.status === "ACTIVE")
              .map((item) => (
                <option key={item.organisation_id} value={item.organisation_id}>
                  {item.organisation_name}
                </option>
              ))}
          </select>
        </label>
      )}
      <nav aria-label="Primary navigation" style={styles.nav}>
        {workspaceLinks
          .filter(([, href]) =>
            canViewRoute(href, membership, user.platform_permissions),
          )
          .map(([label, href]) => (
            <Link
              key={href}
              href={href}
              style={pathname === href ? styles.activeNav : styles.navLink}
            >
              {label}
            </Link>
          ))}
      </nav>
      {demoMode && (
        <div style={styles.banner}>
          Synthetic practice data · no official provider material · local audio
          demonstration unavailable.
        </div>
      )}
      {error && (
        <p role="alert" style={{ padding: "0 2rem", color: "#a42b2b" }}>
          {error}
        </p>
      )}
      <div style={styles.content} key={`${user.id}:${organisationId}`}>
        {canViewRoute(pathname, membership, user.platform_permissions) ? (
          children
        ) : (
          <section role="alert">
            <h1>Access restricted</h1>
            <p>
              Your current organisation role does not provide this workspace.
              API permissions are enforced by the server.
            </p>
            <Link href="/">Return to your overview</Link>
          </section>
        )}
      </div>
    </div>
  );
}

export const styles = {
  app: {
    minHeight: "100vh",
    background: "#f5f7fb",
    color: "#172033",
    fontFamily: "Arial, sans-serif",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "1rem 2rem",
    background: "#14213d",
    color: "white",
  },
  user: {
    display: "flex",
    gap: "1rem",
    alignItems: "center",
    fontSize: "0.9rem",
  },
  nav: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "0.4rem",
    padding: "0.8rem 2rem",
    background: "white",
    borderBottom: "1px solid #dce2ee",
  },
  navLink: {
    padding: "0.55rem 0.75rem",
    color: "#44516b",
    textDecoration: "none",
    borderRadius: "0.4rem",
  },
  activeNav: {
    padding: "0.55rem 0.75rem",
    color: "#14213d",
    textDecoration: "none",
    borderRadius: "0.4rem",
    background: "#e3ebff",
    fontWeight: 700,
  },
  banner: {
    padding: "0.65rem 2rem",
    background: "#fff4d6",
    color: "#694f00",
    fontSize: "0.85rem",
    borderBottom: "1px solid #f0df9c",
  },
  content: { maxWidth: "1180px", margin: "0 auto", padding: "2.5rem 1.5rem" },
  center: {
    minHeight: "100vh",
    display: "grid",
    placeItems: "center",
    padding: "2rem",
    fontFamily: "Arial, sans-serif",
  },
  card: {
    maxWidth: "540px",
    padding: "2.5rem",
    background: "white",
    border: "1px solid #dce2ee",
    borderRadius: "0.8rem",
    boxShadow: "0 12px 32px #14213d14",
  },
  kicker: {
    color: "#4969b2",
    textTransform: "uppercase" as const,
    letterSpacing: "0.12em",
    fontSize: "0.75rem",
    fontWeight: 700,
  },
  primary: {
    display: "inline-block",
    padding: "0.7rem 1rem",
    borderRadius: "0.4rem",
    background: "#3157a6",
    color: "white",
    textDecoration: "none",
    fontWeight: 700,
  },
  linkButton: {
    border: 0,
    background: "transparent",
    color: "white",
    textDecoration: "underline",
    cursor: "pointer",
  },
  badge: {
    marginLeft: "0.7rem",
    padding: "0.25rem 0.45rem",
    borderRadius: "0.3rem",
    background: "#5b8def",
    color: "white",
    fontSize: "0.65rem",
    letterSpacing: "0.08em",
  },
};
