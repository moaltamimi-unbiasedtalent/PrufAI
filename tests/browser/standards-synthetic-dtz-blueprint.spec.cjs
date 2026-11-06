const { test, expect } = require("@playwright/test");
const { actor, clickResponse, headers } = require("./evaluation-fixture.cjs");
const { login, logout, screenshot } = require("./support.cjs");

const standards = "/api/v1/platform/standards";
const dtz = `${standards}/dtz`;
const packVersions = "/api/v1/platform/standard-pack-versions";
const administrator = "platform.admin@demo.prufai.local";
const overviewKey = "gast-dtz-overview";

async function openDtz(page, email) {
  const identity = await login(page, email);
  await page
    .getByRole("link", { name: "Standards & packs", exact: true })
    .click();
  const sources = page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname === `${dtz}/sources` &&
      item.request().method() === "GET",
  );
  await page
    .getByRole("link", { name: "DTZ A2–B1 blueprint", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "DTZ A2–B1 blueprint" }),
  ).toBeVisible();
  const response = await sources;
  expect(response.status()).toBe(200);
  return { identity, sources: await response.json() };
}

function metadataReview(row) {
  // The serial suite runs the existing source-foundation journey first so it
  // can review the shared CEFR record in its original UI. Keep the same receipt
  // when this S2 journey runs alone against a fresh disposable database.
  const cefr = row.key === "cefr-companion-2020";
  return {
    expected_metadata_checksum: row.registered.metadata_checksum,
    decision: "METADATA_ONLY",
    rights_holder: row.rights_holder,
    licence_type: "REFERENCE_ONLY",
    basis_reference: cefr
      ? "s1:official-source-metadata-only"
      : "s2:official-source-metadata-only",
    retention_policy: cefr
      ? "s1:source-catalogue-retention"
      : "s2:source-catalogue-retention",
  };
}

function institutionScope(identity) {
  const membership = identity.profile.memberships.find(
    (item) => item.organisation_slug === "prufai-demo-academy-v2",
  );
  expect(membership?.organisation_id).toBeTruthy();
  return {
    id: membership.organisation_id,
    headers: {
      ...headers(identity),
      "X-PrufAI-Organisation-ID": membership.organisation_id,
    },
  };
}

test("a DTZ operator inspects the source conflict ledger and previews draft rules while publication stays blocked", async ({
  page,
}) => {
  const { identity: author, sources } = await openDtz(page, administrator);
  expect(sources.map((row) => row.key).sort()).toEqual([
    "bamf-dtz-curriculum-2016",
    "cefr-companion-2020",
    "gast-dtz-faq",
    "gast-dtz-information",
    "gast-dtz-overview",
    "gast-dtz-specimen-2024",
    "inttestv-2026-306",
    "intv-2026-218",
  ]);
  await page
    .getByRole("combobox", { name: "DTZ source", exact: true })
    .selectOption(overviewKey);
  const metadata = page.getByRole("article", { name: "DTZ source metadata" });
  await expect(
    metadata.getByRole("heading", { name: "Der DTZ auf einen Blick" }),
  ).toBeVisible();
  await expect(
    metadata.getByRole("link", { name: "Official source", exact: true }),
  ).toHaveAttribute("href", /gast\.de\/.+\/der-dtz-auf-einen-blick$/);
  await expect(metadata).toContainText("Bibliographic reference only");
  const overview = sources.find((row) => row.key === overviewKey);
  if (!overview.registered) {
    const registered = await clickResponse(
      page,
      "Register DTZ source metadata",
      `${dtz}/sources/${overviewKey}/register`,
      "POST",
      200,
    );
    overview.registered = registered.registered;
  }
  expect(overview.registered.metadata_checksum).toMatch(/^[a-f0-9]{64}$/);
  if (!overview.registered.reviewed) {
    const denied = await page.request.post(
      `${standards}/versions/${overview.registered.id}/rights-review`,
      {
        headers: headers(author),
        data: metadataReview(overview),
      },
    );
    expect(denied.status()).toBe(403);
    expect(await denied.json()).toMatchObject({
      detail: "SOURCE_INDEPENDENT_RIGHTS_REVIEW_REQUIRED",
    });
  }
  const registered = [];
  for (const source of sources) {
    if (source.registered) {
      registered.push(source);
      continue;
    }
    const response = await page.request.post(
      `${dtz}/sources/${source.key}/register`,
      {
        headers: headers(author),
      },
    );
    expect(response.status(), source.key).toBe(200);
    registered.push(await response.json());
  }
  await logout(page);

  const reviewer = await login(page, actor("reviewer"));
  expect(reviewer.profile.id).not.toBe(author.profile.id);
  for (const source of registered.filter((row) => !row.registered.reviewed)) {
    const response = await page.request.post(
      `${standards}/versions/${source.registered.id}/rights-review`,
      {
        headers: headers(reviewer),
        data: metadataReview(source),
      },
    );
    expect(response.status(), source.key).toBe(200);
    expect(await response.json()).toMatchObject({
      legal_review_status: "APPROVED",
      usage_rights: "BIBLIOGRAPHIC_REFERENCE_ONLY",
      ingestion_allowed: false,
      redistribution_allowed: false,
    });
  }
  await logout(page);

  const { identity: operator, sources: reviewedSources } = await openDtz(
    page,
    administrator,
  );
  expect(reviewedSources.every((row) => row.registered?.reviewed)).toBe(true);
  await page
    .getByRole("combobox", { name: "DTZ source", exact: true })
    .selectOption(overviewKey);
  const exact = await clickResponse(
    page,
    "Inspect exact DTZ source version",
    `${standards}/versions/${overview.registered.id}`,
    "GET",
    200,
  );
  expect(exact).toMatchObject({
    id: overview.registered.id,
    metadata_checksum: overview.registered.metadata_checksum,
    legal_reviewed_by_user_id: reviewer.profile.id,
    content_mode: "METADATA_ONLY",
    usage_rights: "BIBLIOGRAPHIC_REFERENCE_ONLY",
    sha256: null,
  });
  await expect(
    page.getByRole("region", { name: "Exact DTZ source rights" }),
  ).toContainText("BIBLIOGRAPHIC_REFERENCE_ONLY");

  const draft = await clickResponse(
    page,
    "Create or inspect DTZ draft",
    `${dtz}/blueprints`,
    "POST",
    201,
  );
  expect(draft).toMatchObject({
    status: "DRAFT",
    review_status: "REVIEW_REQUIRED",
    assessment_family: "LANGUAGE",
    exam_family: "DEUTSCH_TEST_FUER_ZUWANDERER",
    language: "de",
    supported_levels: ["A2", "B1"],
    allowed_classifications: ["PRACTICE"],
  });
  expect(draft.checksum).toMatch(/^[a-f0-9]{64}$/);
  expect(draft.sections.map((section) => section.skill)).toEqual([
    "LISTENING",
    "READING",
    "WRITING",
    "SPEAKING",
  ]);
  expect(draft.sections[0]).toMatchObject({
    item_total: 20,
    scoring_domain: "LISTENING_READING",
  });
  expect(draft.sections[1]).toMatchObject({
    item_total: 25,
    scoring_domain: "LISTENING_READING",
  });
  const blueprint = page.getByRole("article", {
    name: "Exact DTZ blueprint version",
  });
  await expect(blueprint).toContainText("A2–B1");
  for (const label of ["Hören", "Lesen", "Schreiben", "Sprechen"]) {
    await expect(
      blueprint.getByRole("heading", { name: label, exact: true }),
    ).toBeVisible();
  }
  const speaking = page.getByRole("region", { name: "SPEAKING", exact: true });
  await expect(
    speaking.getByText(/UNSUPPORTED_FOR_FULL_FIDELITY.*Live examiner/),
  ).toBeVisible();
  await speaking
    .locator("summary")
    .filter({ hasText: "Collaborative planning" })
    .click();
  await expect(
    speaking.getByText("INTERACTIVE_PAIR_EXAM", { exact: true }),
  ).toBeVisible();

  const ledger = page.getByRole("region", {
    name: "Source claim and conflict ledger",
  });
  for (const code of [
    "DTZ_SPEAKING_TASK_COUNT",
    "DTZ_WRITING_CRITERIA",
    "DTZ_SPEAKING_CRITERIA",
  ]) {
    const group = draft.claim_ledger.find((item) => item.code === code);
    expect(group.status).toBe("SOURCE_CONFLICT");
    expect(group.reviewing_expert).toBeNull();
    expect(group.claims.length).toBeGreaterThan(1);
    await expect(
      ledger.getByText(`${code} · SOURCE_CONFLICT`, { exact: true }),
    ).toBeVisible();
  }
  const oralCount = ledger.locator("details").filter({
    has: page.getByText("DTZ_SPEAKING_TASK_COUNT · SOURCE_CONFLICT", {
      exact: true,
    }),
  });
  await oralCount.locator("summary").click();
  await expect(oralCount).toContainText(overview.registered.id);
  await expect(oralCount).toContainText(overview.registered.metadata_checksum);
  await expect(oralCount).toContainText('"oral_tasks":5');
  await expect(oralCount).toContainText('"oral_tasks":3');

  const b1Claim = ledger.locator("details").filter({
    has: page.getByText("DTZ_OVERALL_B1 · SOURCE_VERIFIED", { exact: true }),
  });
  await b1Claim.locator("summary").click();
  const law = b1Claim.locator("li").filter({
    has: page.getByText("inttestv-2026-306", { exact: true }),
  });
  await expect(
    law.getByRole("link", { name: "Exact source edition", exact: true }),
  ).toHaveAttribute(
    "href",
    "https://www.recht.bund.de/bgbl/1/2026/306/regelungstext.pdf?__blob=publicationFile&v=1",
  );
  await expect(
    law.getByRole("link", { name: "Cited section", exact: true }),
  ).toHaveAttribute(
    "href",
    "https://www.gesetze-im-internet.de/inttestv/__10.html",
  );

  const rubrics = page.getByRole("region", {
    name: "Writing and Speaking rubrics",
  });
  for (const skill of ["WRITING", "SPEAKING"]) {
    const rubric = draft.rubrics.find((item) => item.skill === skill);
    expect(rubric).toMatchObject({
      review_status: "REVIEW_REQUIRED",
      assessment_mode: "HUMAN_ONLY",
    });
    await expect(
      rubrics.getByText(`${skill} · ${rubric.status} · REVIEW_REQUIRED`, {
        exact: true,
      }),
    ).toBeVisible();
  }
  const rule = draft.rules.find(
    (item) => item.code === "PRUFAI_DTZ_S2_OVERALL_B1",
  );
  const legal = reviewedSources.find(
    (item) => item.key === "inttestv-2026-306",
  );
  expect(rule.sources).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        source_version_id: legal.registered.id,
        metadata_checksum: legal.registered.metadata_checksum,
      }),
    ]),
  );
  const scoring = page.getByRole("region", {
    name: "Source-pinned draft scoring rules",
  });
  await scoring.locator("summary").filter({ hasText: rule.name }).click();
  await expect(scoring.getByText(rule.checksum, { exact: true })).toBeVisible();
  await expect(
    scoring.getByText("1 Nov 2026 (Europe/Berlin)", { exact: true }),
  ).toBeVisible();

  await page
    .getByLabel("Applicability date", { exact: true })
    .fill("2026-11-06");
  await page
    .getByRole("combobox", { name: "Listening / Reading", exact: true })
    .selectOption("A2");
  await page
    .getByRole("combobox", { name: "Writing", exact: true })
    .selectOption("B1");
  await page
    .getByRole("combobox", { name: "Speaking", exact: true })
    .selectOption("B1");
  const previewPath = `${dtz}/blueprints/${draft.id}/preview`;
  const initialPreview = await clickResponse(
    page,
    "Run technical preview",
    previewPath,
    "POST",
    200,
  );
  expect(initialPreview).toMatchObject({
    execution_class: "DRAFT_PREVIEW",
    outcome: "B1",
    reason: "B1_DRAFT_RULE_MATCH",
    rule: { id: rule.id, checksum: rule.checksum },
  });
  const previewOutput = page.getByLabel("Technical preview result", {
    exact: true,
  });
  await expect(previewOutput).toHaveText(
    "DRAFT_PREVIEW · B1 · B1_DRAFT_RULE_MATCH",
  );
  await page
    .getByRole("combobox", { name: "Synthetic objective answer", exact: true })
    .selectOption("B");
  await page
    .getByRole("combobox", { name: "Listening / Reading", exact: true })
    .selectOption("BELOW_A2");
  const below = await clickResponse(
    page,
    "Run technical preview",
    previewPath,
    "POST",
    200,
  );
  expect(below).toMatchObject({
    outcome: "UNDETERMINED",
    reason: "B1_DRAFT_RULE_NOT_MET",
    objective: {
      status: "SCORED",
      awarded_points: "1",
      combined_maximum_points: 45,
    },
  });
  await expect(previewOutput).toContainText("UNDETERMINED");
  await page
    .getByRole("combobox", { name: "Writing", exact: true })
    .selectOption("");
  const missing = await clickResponse(
    page,
    "Run technical preview",
    previewPath,
    "POST",
    200,
  );
  expect(missing).toMatchObject({
    outcome: "UNDETERMINED",
    reason: "MISSING_OR_UNDETERMINED_DOMAIN",
  });

  const pack = await clickResponse(
    page,
    "Open exact Standard Pack version",
    `${packVersions}/${draft.id}`,
    "GET",
    200,
  );
  expect(pack).toMatchObject({
    id: draft.id,
    status: "DRAFT",
    checksum: draft.checksum,
  });
  await expect(
    page.getByRole("article", { name: "DTZ Standard Pack version" }),
  ).toContainText(draft.checksum);
  const denial = await clickResponse(
    page,
    "Request DTZ pack publication",
    `${packVersions}/${draft.id}/publish`,
    "POST",
    409,
  );
  expect(denial).toMatchObject({ detail: "DTZ_DRAFT_PUBLICATION_BLOCKED" });
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "DTZ_DRAFT_PUBLICATION_BLOCKED" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Human review and publication blockers" }),
  ).toContainText("criteria require reconciliation");
  await screenshot(page, "s2-dtz-blueprint-review-blockers.png");
  const history = await clickResponse(
    page,
    `Inspect DTZ version ${draft.version_label}`,
    `${dtz}/blueprints/${draft.id}`,
    "GET",
    200,
  );
  expect(history).toMatchObject({
    id: draft.id,
    status: "DRAFT",
    checksum: draft.checksum,
  });
  const afterDenial = await page.request.get(`${packVersions}/${draft.id}`, {
    headers: headers(operator),
  });
  expect(afterDenial.status()).toBe(200);
  expect(await afterDenial.json()).toMatchObject({
    status: "DRAFT",
    checksum: draft.checksum,
  });
  await logout(page);

  const institution = institutionScope(
    await login(page, "admin@demo.prufai.local"),
  );
  const visible = await page.request.get(
    `/api/v1/organisations/${institution.id}/standards/dtz`,
    { headers: institution.headers },
  );
  expect(visible.status()).toBe(200);
  expect(await visible.json()).toMatchObject({ published_versions: [] });
  const institutionDenied = await page.request.get(
    `${dtz}/blueprints/${draft.id}`,
    { headers: institution.headers },
  );
  expect(institutionDenied.status()).toBe(403);
  const rightsDenied = await page.request.post(
    `${standards}/versions/${overview.registered.id}/rights-review`,
    {
      headers: institution.headers,
      data: metadataReview(overview),
    },
  );
  expect(rightsDenied.status()).toBe(403);
  await logout(page);

  const candidate = institutionScope(
    await login(page, "golden.candidate@demo.prufai.local"),
  );
  const protectedEvidence = await page.request.get(
    `${standards}/versions/${overview.registered.id}/integrity`,
    { headers: candidate.headers },
  );
  expect(protectedEvidence.status()).toBe(403);
  const retrieval = await page.request.post(`${standards}/retrieval/search`, {
    headers: candidate.headers,
    data: {
      query: "DTZ source evidence",
      scope: { document_version_ids: [overview.registered.id] },
    },
  });
  expect(retrieval.status()).toBe(403);
  await logout(page);
});
