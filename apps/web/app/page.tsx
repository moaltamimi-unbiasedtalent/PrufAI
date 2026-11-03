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
        {membership?.organisation_name ?? "Select an organisation to continue."}
      </p>
      <section
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))",
          gap: "1rem",
          marginTop: "2rem",
        }}
      >
        {workspaceLinks
          .filter(([, href]) => href !== "/" && canViewRoute(href, membership))
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
          The available workspaces follow your active organisation membership.
          The server checks permission and ownership for every API request.
        </p>
        {demoMode && (
          <p>
            The local demo includes an original objective practice exam and
            institution inspection of submitted results. Audio playback,
            transcription and Writing evidence are unavailable in this dataset.
            Full Control Centre is deferred to a separate milestone.
          </p>
        )}
        <p>
          <strong>Current account:</strong> {user?.email}
        </p>
      </section>
    </div>
  );
}
