"use client";

import Link from "next/link";
import { useAuth, styles } from "../lib/auth";
import { canViewRoute, workspaceLinks } from "../lib/access";

export default function Home() {
  const { user, membership, demoMode } = useAuth();
  return (
    <div>
      <p style={styles.kicker}>Governed assessment workspace</p>
      <h1 style={{ fontSize: "2.7rem", marginBottom: "0.5rem" }}>
        Welcome to PrüfAI{user?.display_name ? `, ${user.display_name}` : ""}
      </h1>
      <p style={{ maxWidth: "700px", fontSize: "1.1rem", lineHeight: 1.6 }}>
        {membership?.organisation_name ??
          (user?.scoped_source_review
            ? "Open your scoped source review workspace to continue."
            : "Select an organisation to continue.")}
      </p>
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))",
          gap: "1rem",
          marginTop: "2rem",
        }}
      >
        {user?.platform_permissions.includes(
          "platform.control_center.read",
        ) && (
          <article style={styles.card}>
            <h2 style={{ marginTop: 0 }}>PrüfAI Control Centre</h2>
            <p>
              Inspect platform governance, audit evidence and system metadata.
            </p>
            <Link style={styles.primary} href="/control-center">
              Open Control Centre →
            </Link>
          </article>
        )}
        {workspaceLinks
          .filter(
            ([, href]) =>
              href !== "/" &&
              canViewRoute(href, membership, [], user?.scoped_source_review),
          )
          .map(([title, href]) => (
            <article key={href} style={styles.card}>
              <h2 style={{ marginTop: 0 }}>{title}</h2>
              <p style={{ lineHeight: 1.5 }}>
                Open the {title.toLowerCase()} workspace.
              </p>
              <Link style={styles.primary} href={href}>
                Open {title.toLowerCase()} →
              </Link>
            </article>
          ))}
      </section>
      <section style={{ ...styles.card, marginTop: "2rem", maxWidth: "none" }}>
        <h2>Workspace access</h2>
        <p>
          Your workspaces depend on your account’s roles and access grants. The
          server checks permission and ownership for every API request.
        </p>
        {demoMode && (
          <p>
            The local demo includes an original objective practice exam and
            institution inspection of submitted results. Audio playback,
            transcription and Writing evidence are unavailable in this dataset.
            Platform operators can inspect governance metadata in the Control
            Centre.
          </p>
        )}
        <p>
          <strong>Current account:</strong> {user?.email}
        </p>
      </section>
    </div>
  );
}
