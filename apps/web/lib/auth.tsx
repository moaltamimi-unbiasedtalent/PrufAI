"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { ACCESS_TOKEN_KEY, authenticatedFetch } from "./api";

export type DemoUser = {
  id: string;
  email: string;
  display_name: string | null;
  memberships: Array<{
    organisation_id: string;
    organisation_name?: string;
    status: string;
    role_keys: string[];
  }>;
  platform_scope: boolean;
};

type AuthContextValue = {
  user: DemoUser | null;
  loading: boolean;
  signOut: () => void;
};
const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  signOut: () => undefined,
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<DemoUser | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    authenticatedFetch("/api/v1/auth/me")
      .then((response) => (response.ok ? response.json() : null))
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);
  const value = useMemo(
    () => ({
      user,
      loading,
      signOut: () => {
        window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
        setUser(null);
      },
    }),
    [loading, user],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

const links = [
  ["Overview", "/"],
  ["Candidate exam", "/candidate"],
  ["Exam Studio", "/exam-studio"],
  ["Result moderation", "/institution/result-moderation"],
  ["Appeals", "/institution/appeals"],
  ["Writing review", "/institution/writing-reviews"],
  ["Speaking review", "/institution/speaking-reviews"],
] as const;

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, signOut } = useAuth();
  if (pathname === "/login") return <>{children}</>;
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
          <p style={styles.kicker}>PrüfAI local demo</p>
          <h1>Sign in to the platform</h1>
          <p>
            Use one of the synthetic demo accounts to open the governed
            workspace.
          </p>
          <Link style={styles.primary} href="/login">
            Open sign in
          </Link>
        </section>
      </main>
    );
  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <div>
          <strong>PrüfAI</strong>
          <span style={styles.badge}>LOCAL DEMO</span>
        </div>
        <div style={styles.user}>
          <span>{user.display_name ?? user.email}</span>
          <button
            style={styles.linkButton}
            onClick={() => {
              signOut();
              router.push("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <nav aria-label="Primary navigation" style={styles.nav}>
        {links.map(([label, href]) => (
          <Link
            key={href}
            href={href}
            style={pathname === href ? styles.activeNav : styles.navLink}
          >
            {label}
          </Link>
        ))}
      </nav>
      <div style={styles.banner}>
        Synthetic walkthrough data only · no official provider material · Phase
        8 Step 2 remains on its feature branch.
      </div>
      <main style={styles.content}>{children}</main>
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
