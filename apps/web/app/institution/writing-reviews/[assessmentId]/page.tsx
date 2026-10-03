type Props = { params: Promise<{ assessmentId: string }> };

export default async function WritingReviewWorkspacePage({ params }: Props) {
  const { assessmentId } = await params;
  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "3rem 1.5rem" }}>
      <p style={{ letterSpacing: "0.08em", textTransform: "uppercase" }}>
        Institution review
      </p>
      <h1>Writing assessment {assessmentId}</h1>
      <p>
        Candidate writing is rendered as inert text. Server authorisation and
        checksum checks remain authoritative.
      </p>
      <section aria-labelledby="proposal-heading">
        <h2 id="proposal-heading">AI PROPOSAL</h2>
        <p>No proposal loaded in this preview.</p>
      </section>
      <section aria-labelledby="human-heading">
        <h2 id="human-heading">HUMAN APPROVED VALUE</h2>
        <p>
          Nothing is approved until a reviewer confirms each bounded criterion.
        </p>
      </section>
      <fieldset>
        <legend>Review action</legend>
        <button type="button">Approve</button>
        <button type="button">Request changes</button>
        <button type="button">Reject</button>
      </fieldset>
    </main>
  );
}
