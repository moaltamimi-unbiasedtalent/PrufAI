const { test, expect } = require("@playwright/test");
const { createHash, randomUUID } = require("node:crypto");
const { login, logout, screenshot, restartOwnedApi } = require("./support.cjs");
const { actor, clickResponse, headers } = require("./evaluation-fixture.cjs");

const source = "/api/v1/platform/standards";
const packs = "/api/v1/platform/standard-pack-versions";
const adminEmail = "platform.admin@demo.prufai.local";
const reviewerEmail = actor("reviewer");
const content = Buffer.from(
  "Original synthetic source created for the S1 browser durability check. " +
    "An ordinary bicycle repair shop explains how to book a repair appointment and describe a broken wheel. " +
    "This is a technical fixture and contains no official assessment content.\n",
);
const sha = createHash("sha256").update(content).digest("hex");

async function operator(page, email, section = "Source Registry") {
  const identity = await login(page, email);
  await page.getByRole("link", { name: section, exact: true }).click();
  return identity;
}

async function createSyntheticSource(page, identity) {
  const suffix = randomUUID().replaceAll("-", "");
  async function create(relative, data) {
    const response = await page.request.post(`${source}${relative}`, {
      headers: headers(identity),
      data,
    });
    expect(response.status()).toBe(201);
    return response.json();
  }
  const authority = await create("/authorities", {
    code: `S1_SYNTHETIC_${suffix}`,
    name: "Original synthetic S1 technical source owner",
    authority_type: "INTERNAL",
  });
  const registered = await create("/sources", {
    authority_id: authority.id,
    source_code: `S1_ORIGINAL_${suffix}`,
    title: "Original synthetic bicycle repair document",
    document_type: "PRACTICE_MATERIAL",
    assessment_family: "LANGUAGE",
    language: "de",
    cefr_level: "B1",
    skill: "WRITING",
  });
  return create(`/sources/${registered.id}/versions`, {
    version_label: "original-synthetic-v1",
    content_mode: "ORIGINAL_SYNTHETIC",
    rights_holder: "PrüfAI original synthetic technical fixture",
    licence_type: "OWNED_ORIGINAL",
  });
}

async function inspectSource(page, id) {
  await page.getByLabel("Source version reference", { exact: true }).fill(id);
  const version = await clickResponse(
    page,
    "Inspect source version",
    `${source}/versions/${id}`,
    "GET",
    200,
  );
  await expect(
    page.getByRole("article", { name: "Source version detail", exact: true }),
  ).toContainText(id);
  return version;
}

async function decideRights(page, version, decision) {
  await page
    .getByRole("combobox", { name: "Rights decision", exact: true })
    .selectOption(decision);
  await page
    .getByLabel("Rights holder", { exact: true })
    .fill(
      version.rights_holder || "PrüfAI original synthetic technical fixture",
    );
  await page
    .getByLabel("Licence type", { exact: true })
    .fill(decision === "METADATA_ONLY" ? "REFERENCE_ONLY" : "OWNED_ORIGINAL");
  await page
    .getByLabel("Rights basis reference", { exact: true })
    .fill(
      decision === "METADATA_ONLY"
        ? "s1:official-source-metadata-only"
        : "s1:original-synthetic-fixture-rights",
    );
  await page
    .getByLabel("Retention policy reference", { exact: true })
    .fill(
      decision === "METADATA_ONLY"
        ? "s1:source-catalogue-retention"
        : "s1:technical-fixture-retention",
    );
  if (decision !== "METADATA_ONLY") {
    await page.getByLabel("Approved file SHA-256", { exact: true }).fill(sha);
  }
  const rights = await clickResponse(
    page,
    "Record rights decision",
    `${source}/versions/${version.id}/rights-review`,
    "POST",
    200,
  );
  await expect(
    page.getByRole("status").filter({
      hasText: "Rights decision recorded for this exact source version.",
    }),
  ).toBeVisible();
  return rights;
}

async function findCitation(page, versionId) {
  await page
    .getByLabel("Search approved exact source", { exact: true })
    .fill("bicycle repair appointment");
  const result = await clickResponse(
    page,
    "Find exact citations",
    `${source}/retrieval/search`,
    "POST",
    200,
  );
  expect(result).toMatchObject({
    mode: "HYBRID",
    execution_class: "TEST_ONLY",
  });
  expect(result.results.length).toBeGreaterThan(0);
  const citation = result.results[0].citation;
  expect(citation).toMatchObject({
    document_version_id: versionId,
    checksum: sha,
  });
  await expect(
    page.getByRole("region", { name: "Exact version citations", exact: true }),
  ).toContainText(citation.citation_id);
  return citation;
}

test("operators register official metadata and verify durable exact-source evidence after an API restart", async ({
  page,
}) => {
  const author = await operator(page, adminEmail);
  await page
    .getByRole("combobox", { name: "Official publication", exact: true })
    .selectOption("cefr-companion-2020");
  const official = await clickResponse(
    page,
    "Register official metadata",
    `${source}/catalogue/cefr-companion-2020/register`,
    "POST",
    200,
  );
  expect(official).toMatchObject({
    content_mode: "METADATA_ONLY",
    sha256: null,
  });
  expect(official.metadata_checksum).toMatch(/^[a-f0-9]{64}$/);
  const sourceDetail = page.getByRole("article", {
    name: "Source version detail",
    exact: true,
  });
  await expect(sourceDetail).toContainText("No file registered");
  await expect(sourceDetail).toContainText(official.metadata_checksum);
  const synthetic = await createSyntheticSource(page, author);
  const preRights = await page.request.post(
    `${source}/versions/${synthetic.id}/file`,
    {
      headers: headers(author),
      data: {
        filename: "original-synthetic.txt",
        content_base64: content.toString("base64"),
        declared_mime_type: "text/plain",
      },
    },
  );
  expect(preRights.status()).toBe(409);
  expect(await preRights.json()).toMatchObject({
    detail: "SOURCE_CONTENT_RIGHTS_REQUIRED",
  });
  await logout(page);

  await operator(page, reviewerEmail);
  const metadata = await inspectSource(page, official.document_version_id);
  expect(metadata.ingestion_allowed).toBe(false);
  await decideRights(page, metadata, "METADATA_ONLY");
  await expect(sourceDetail).toContainText(
    "File upload, ingestion and content search are unavailable",
  );
  const syntheticVersion = await inspectSource(page, synthetic.id);
  expect(syntheticVersion.metadata_checksum).toMatch(/^[a-f0-9]{64}$/);
  await decideRights(page, syntheticVersion, "ALLOW_SYNTHETIC_INGESTION");
  await expect(sourceDetail).toContainText(
    "Approved for exact synthetic bytes",
  );
  await logout(page);

  await operator(page, adminEmail);
  await inspectSource(page, synthetic.id);
  await page
    .getByLabel("Original synthetic document", { exact: true })
    .setInputFiles({
      name: "original-synthetic.txt",
      mimeType: "text/plain",
      buffer: content,
    });
  const uploaded = await clickResponse(
    page,
    "Upload private file",
    `${source}/versions/${synthetic.id}/file`,
    "POST",
    200,
  );
  expect(uploaded.sha256).toBe(sha);
  const ingestion = await clickResponse(
    page,
    "Ingest exact source version",
    `${source}/versions/${synthetic.id}/ingest`,
    "POST",
    200,
  );
  expect(ingestion).toMatchObject({ status: "SUCCEEDED" });
  expect(ingestion.pages_detected).toBeGreaterThan(0);
  await logout(page);

  const reviewer = await operator(page, reviewerEmail);
  await inspectSource(page, synthetic.id);
  await clickResponse(
    page,
    "Approve ingested source version",
    `${source}/versions/${synthetic.id}/approve`,
    "POST",
    200,
  );
  const indexed = await clickResponse(
    page,
    "Index exact source version",
    `${source}/retrieval/index`,
    "POST",
    200,
  );
  expect(indexed).toMatchObject({
    index_status: "READY",
    execution_class: "TEST_ONLY",
  });
  expect(indexed.chunks_created).toBeGreaterThan(0);
  expect(indexed.embeddings_created).toBeGreaterThan(0);
  const before = await findCitation(page, synthetic.id);
  await restartOwnedApi();
  await page.reload();
  const afterRestart = await inspectSource(page, synthetic.id);
  expect(afterRestart).toMatchObject({
    sha256: sha,
    status: "APPROVED",
    ingestion_allowed: true,
  });
  await expect(sourceDetail).toContainText("SUCCEEDED");
  const integrity = await clickResponse(
    page,
    "Verify private storage integrity",
    `${source}/versions/${synthetic.id}/integrity`,
    "GET",
    200,
  );
  expect(integrity).toMatchObject({
    integrity: "VERIFIED",
    storage_backend: "S3_PRIVATE",
    sha256: sha,
    file_size: content.length,
  });
  await expect(sourceDetail).toContainText(
    "Private storage integrity VERIFIED",
  );
  const after = await findCitation(page, synthetic.id);
  expect(after.citation_id).toBe(before.citation_id);
  await screenshot(page, "s1-source-durable-exact-citation.png");

  await page
    .getByRole("link", { name: "Standards & packs", exact: true })
    .click();
  await page
    .getByLabel("Reviewed CEFR source version", { exact: true })
    .fill(official.document_version_id);
  await page
    .getByLabel("Reviewed source metadata checksum", { exact: true })
    .fill(official.metadata_checksum);
  const draft = await clickResponse(
    page,
    "Create or inspect B1 draft",
    "/api/v1/platform/standard-packs/cefr-b1-foundation",
    "POST",
    201,
  );
  expect(draft).toMatchObject({
    foundation: true,
    status: "DRAFT",
    assessment_family: "LANGUAGE",
    language: "de",
    supported_levels: ["B1"],
    allowed_classifications: ["PRACTICE"],
  });
  expect(draft.source_bindings).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        document_version_id: official.document_version_id,
        source_metadata_checksum: official.metadata_checksum,
      }),
    ]),
  );
  expect(draft.publication_blockers.length).toBeGreaterThan(0);
  const packDetail = page.getByRole("article", {
    name: "Exact pack version detail",
    exact: true,
  });
  await expect(packDetail).toContainText(official.metadata_checksum);
  await expect(packDetail).toContainText("Publication blocked");
  const publication = await page.request.post(`${packs}/${draft.id}/publish`, {
    headers: headers(reviewer),
  });
  expect(publication.status()).toBe(409);
  const unchanged = await page.request.get(`${packs}/${draft.id}`, {
    headers: headers(reviewer),
  });
  expect(unchanged.status()).toBe(200);
  expect(await unchanged.json()).toMatchObject({
    status: "DRAFT",
    checksum: draft.checksum,
  });
  await screenshot(page, "s1-foundation-draft-publication-blocked.png");
  await logout(page);

  for (const email of [
    "golden.candidate@demo.prufai.local",
    "admin@demo.prufai.local",
  ]) {
    const unauthorized = await login(page, email);
    const institution = unauthorized.profile.memberships.find(
      (membership) => membership.organisation_slug === "prufai-demo-academy-v2",
    );
    expect(institution?.organisation_id).toBeTruthy();
    const scopedHeaders = {
      ...headers(unauthorized),
      "X-PrufAI-Organisation-ID": institution.organisation_id,
    };
    const privateVersion = await page.request.get(
      `${source}/versions/${synthetic.id}/integrity`,
      { headers: scopedHeaders },
    );
    expect(privateVersion.status()).toBe(403);
    const retrieval = await page.request.post(`${source}/retrieval/search`, {
      headers: scopedHeaders,
      data: {
        query: "repair appointment",
        scope: { document_version_ids: [synthetic.id] },
      },
    });
    expect(retrieval.status()).toBe(403);
    expect(await retrieval.text()).not.toContain("bicycle repair shop");
    await logout(page);
  }
});
