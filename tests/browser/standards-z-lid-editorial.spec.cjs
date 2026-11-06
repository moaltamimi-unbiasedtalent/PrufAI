const { randomUUID } = require("node:crypto");
const { expect, test } = require("@playwright/test");
const { actor, clickResponse, headers } = require("./evaluation-fixture.cjs");
const { apiResponse, login, logout } = require("./support.cjs");

const standards = "/api/v1/platform/standards";
const lid = `${standards}/lid`;
const collections = `${lid}/editorial/collections`;
const evaluation = "/api/v1/platform/evaluation";
const workbench = "/control-center/standards/lid/editorial";
const candidateEmail = "candidate@demo.prufai.local";

function organisation(identity) {
  const membership = identity.profile.memberships.find(
    ({ organisation_slug, status }) =>
      organisation_slug === "prufai-demo-academy-v2" && status === "ACTIVE",
  );
  expect(membership).toBeTruthy();
  return membership.organisation_id;
}

function scoped(identity, organisationId) {
  return { ...headers(identity), "X-PrufAI-Organisation-ID": organisationId };
}

async function checked(response, status) {
  expect(response.status(), await response.text()).toBe(status);
  return response.json();
}

async function ensureBlueprint(page, editor) {
  const existing = await checked(
    await page.request.get(`${lid}/blueprints`, { headers: headers(editor) }),
    200,
  );
  if (existing.length) return { blueprint: existing[0], editor };

  const sources = await checked(
    await page.request.get(`${lid}/sources`, { headers: headers(editor) }),
    200,
  );
  for (const source of sources) {
    if (source.registered) continue;
    const registered = await checked(
      await page.request.post(`${lid}/sources/${source.key}/register`, {
        headers: headers(editor),
      }),
      200,
    );
    source.registered = registered.registered;
  }
  await logout(page);
  const rightsReviewer = await login(page, actor("reviewer"));
  for (const source of sources) {
    if (source.registered.reviewed) continue;
    await checked(
      await page.request.post(
        `${standards}/versions/${source.registered.id}/rights-review`,
        {
          headers: headers(rightsReviewer),
          data: {
            expected_metadata_checksum: source.registered.metadata_checksum,
            decision: "METADATA_ONLY",
            rights_holder: source.rights_holder,
            licence_type: "REFERENCE_ONLY",
            basis_reference: "s4-browser:blueprint-metadata-only",
            retention_policy: "s4-browser:isolated-acceptance",
          },
        },
      ),
      200,
    );
  }
  await logout(page);
  const currentEditor = await login(page, actor("source"));
  const blueprint = await checked(
    await page.request.post(`${lid}/blueprints`, {
      headers: headers(currentEditor),
      data: {
        sources: Object.fromEntries(
          sources.map(({ key, registered }) => [
            key,
            {
              version_id: registered.id,
              metadata_checksum: registered.metadata_checksum,
            },
          ]),
        ),
      },
    }),
    201,
  );
  return { blueprint, editor: currentEditor };
}

async function openFromStandards(page) {
  await page
    .getByRole("link", { name: "Standards & packs", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Leben in Deutschland blueprint", exact: true })
    .click();
  await page
    .getByRole("link", {
      name: "Original Berlin LiD editorial set",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Original Berlin LiD editorial set",
      exact: true,
    }),
  ).toBeVisible();
}

async function inspectItem(page, collection, item) {
  const select = page.getByRole("combobox", {
    name: "Editorial collection",
    exact: true,
  });
  if ((await select.inputValue()) !== collection.id) {
    const opening = apiResponse(page, `${collections}/${collection.id}`);
    await select.selectOption(collection.id);
    expect((await opening).status()).toBe(200);
  }
  const exact = apiResponse(
    page,
    `${collections}/${collection.id}/items/${item.item_version_id}`,
  );
  await page
    .getByRole("combobox", { name: "Exact editorial item", exact: true })
    .selectOption(item.item_version_id);
  return checked(await exact, 200);
}

test("LiD editors preview original Berlin drafts and record an independent TEST ONLY review", async ({
  page,
}) => {
  let editor = await login(page, actor("source"));
  const organisationId = organisation(editor);
  const baseline = await ensureBlueprint(page, editor);
  editor = baseline.editor;
  const { blueprint } = baseline;

  let collection;
  let item;
  await test.step("Create and inspect the real tenant editorial draft", async () => {
    await openFromStandards(page);
    await page
      .getByRole("combobox", { name: "Exact LiD blueprint", exact: true })
      .selectOption(blueprint.id);
    collection = await clickResponse(
      page,
      "Create Berlin editorial draft",
      collections,
      "POST",
      201,
    );
    expect(collection).toMatchObject({
      organisation_id: organisationId,
      blueprint_version_id: blueprint.id,
      pack: { status: "DRAFT" },
      counts: {
        drafted: 33,
        general_pool_eligible: 0,
        berlin_pool_eligible: 0,
        published: 0,
      },
    });
    expect(
      collection.items.filter(({ scope }) => scope === "GENERAL_DE"),
    ).toHaveLength(30);
    expect(
      collection.items.filter(({ jurisdiction }) => jurisdiction === "DE-BE"),
    ).toHaveLength(3);
    expect(collection.blockers.length).toBeGreaterThan(0);
    await expect(
      page.getByRole("region", { name: "Practice publication blockers" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Publish approved practice pack" }),
    ).toHaveCount(0);

    item = await inspectItem(page, collection, collection.items[0]);
    expect(item.options).toHaveLength(4);
    expect(new Set(item.options.map(({ option_id }) => option_id)).size).toBe(
      4,
    );
    expect(
      item.options.filter(
        ({ option_id }) => option_id === item.correct_option_id,
      ),
    ).toHaveLength(1);
    expect(item.checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(item.provenance_checksum).toMatch(/^[a-f0-9]{64}$/);
    await expect(
      page.getByRole("article", { name: "Exact editorial item" }),
    ).toContainText("AI_ASSISTED_DRAFT");
    await expect(
      page
        .getByRole("region", { name: "Factual source evidence" })
        .getByRole("link")
        .first(),
    ).toHaveAttribute("href", /^https:\/\//);
    await expect(
      page.getByText("Proposed answer", { exact: true }),
    ).toBeVisible();
  });

  await test.step("Preview all 33 pinned versions and submit the exact item", async () => {
    const preview = await clickResponse(
      page,
      "Open pinned Berlin editorial preview",
      `${collections}/${collection.id}/preview`,
      "POST",
      200,
    );
    expect(preview).toMatchObject({
      mode: "CONTENT_EDITORIAL_PREVIEW",
      bundesland: "DE-BE",
      collection_checksum: collection.checksum,
      blueprint_version_id: blueprint.id,
      pack_version_id: collection.pack.id,
      duration_seconds: 3600,
    });
    expect(preview.checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(preview.items).toHaveLength(33);
    expect(
      preview.items.filter(({ scope }) => scope === "GENERAL_DE"),
    ).toHaveLength(30);
    expect(
      preview.items.filter(({ jurisdiction }) => jurisdiction === "DE-BE"),
    ).toHaveLength(3);
    expect(new Set(preview.items.map(({ item_id }) => item_id)).size).toBe(33);
    expect(
      preview.items
        .map(({ item_version_id, checksum, provenance_checksum }) => [
          item_version_id,
          checksum,
          provenance_checksum,
        ])
        .sort(),
    ).toEqual(
      collection.items
        .map(({ item_version_id, checksum, provenance_checksum }) => [
          item_version_id,
          checksum,
          provenance_checksum,
        ])
        .sort(),
    );
    await expect(
      page
        .getByRole("list", { name: "Pinned editorial preview slots" })
        .locator(":scope > li"),
    ).toHaveCount(33);

    const submitted = await clickResponse(
      page,
      "Submit item for factual review",
      `${collections}/${collection.id}/items/${item.item_version_id}/review/submit`,
      "POST",
      200,
    );
    expect(submitted).toMatchObject({
      item_version_id: item.item_version_id,
      item_checksum: item.checksum,
    });
    await expect(
      page.getByRole("status", { name: "Editorial review receipt" }),
    ).toContainText(submitted.id);
    await expect(
      page.getByRole("button", { name: "Record factual decision" }),
    ).toHaveCount(0);

    const official = await page.request.post(
      `/api/v1/organisations/${organisationId}/certification-authorizations`,
      {
        headers: scoped(editor, organisationId),
        data: {
          assessment_scope: "OFFICIAL_CERTIFICATION",
          provider_scope: collection.pack.code,
          standard_pack_version_id: collection.pack.id,
          evidence_reference: "s4-browser:official-authorization-negative",
          effective_from: new Date().toISOString(),
          operation_id: randomUUID(),
        },
      },
    );
    expect(await checked(official, 409)).toMatchObject({
      detail: "CERTIFICATION.NOT_AUTHORIZED",
    });
    await logout(page);
  });

  await test.step("Record an independently qualified TEST ONLY decision", async () => {
    const examiner = await login(page, actor("examiner-a"));
    expect(examiner.profile.id).not.toBe(editor.profile.id);
    await logout(page);
    const manager = await login(page, actor("reviewer"));
    const qualification = await checked(
      await page.request.post(`${evaluation}/examiner-qualifications`, {
        headers: headers(manager),
        data: {
          operation_id: randomUUID(),
          examiner_id: examiner.profile.id,
          organisation_id: organisationId,
          kind: "LID_FACTUAL",
          language: "de",
          standard_pack_version_id: collection.pack.id,
          standard_pack_checksum: collection.pack.checksum,
          origin: "SYNTHETIC_TEST",
          credential_reference: "synthetic:s4-browser-factual-review",
          expires_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
        },
      }),
      201,
    );
    await logout(page);
    const approver = await login(page, actor("adjudicator"));
    await checked(
      await page.request.post(
        `${evaluation}/examiner-qualifications/${qualification.id}/approve`,
        {
          headers: headers(approver),
          data: {
            expected_checksum: qualification.checksum,
            review_reference:
              "synthetic:s4-browser-independent-qualification-approval",
          },
        },
      ),
      200,
    );
    await logout(page);

    const reviewer = await login(page, actor("examiner-a"));
    await page.goto(workbench);
    const before = await inspectItem(page, collection, item);
    expect(before.checksum).toBe(item.checksum);
    const form = page.getByRole("form", {
      name: "Factual review",
      exact: true,
    });
    await expect(
      form.getByRole("list", { name: "Review checklist" }),
    ).toContainText("factual accuracy");
    await expect(
      form.getByRole("combobox", {
        name: "Approved qualification",
        exact: true,
      }),
    ).toContainText("TEST ONLY");
    await form
      .getByRole("combobox", { name: "Decision", exact: true })
      .selectOption("REJECT");
    const privateReason =
      "Synthetic browser rejection: independent workflow evidence only.";
    await form
      .getByRole("textbox", { name: "Private review reason", exact: true })
      .fill(privateReason);
    const decision = await clickResponse(
      page,
      "Record factual decision",
      `${collections}/${collection.id}/items/${item.item_version_id}/review/decision`,
      "POST",
      200,
    );
    expect(decision).toMatchObject({
      decision: "REJECT",
      evidence_class: "SYNTHETIC_TEST",
      actor_user_id: reviewer.profile.id,
      item_version_id: item.item_version_id,
      item_checksum: item.checksum,
    });
    expect(JSON.stringify(decision)).not.toContain(privateReason);
    await expect(
      page.getByRole("status", { name: "Editorial review receipt" }),
    ).toContainText("TEST ONLY");
    await page.reload();
    const stored = await inspectItem(page, collection, item);
    expect(stored.checksum).toBe(item.checksum);
    expect(stored.review_receipts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: decision.id,
          checksum: decision.checksum,
        }),
      ]),
    );
    await expect(
      page.getByRole("region", { name: "Immutable editorial review receipts" }),
    ).toContainText("TEST ONLY");
    await expect(
      page.getByRole("region", { name: "Immutable editorial review receipts" }),
    ).not.toContainText(privateReason);
    await expect(
      page.getByRole("button", { name: "Publish approved practice pack" }),
    ).toHaveCount(0);
    const reviewCounts = await checked(
      await page.request.get(`${collections}/${collection.id}`, {
        headers: headers(reviewer),
      }),
      200,
    );
    expect(reviewCounts.counts).toMatchObject({
      drafted: 33,
      rejected: 1,
      test_review_decisions: 1,
      approved: 0,
      published: 0,
    });
    await logout(page);
  });

  await test.step("Revise through a new immutable version and inspect the old decision", async () => {
    await login(page, actor("source"));
    await page.goto(workbench);
    const rejected = await inspectItem(page, collection, item);
    expect(rejected.maturity).toBe("REJECTED");
    expect(rejected.provenance_checksum).toBe(item.provenance_checksum);
    const form = page.getByRole("form", {
      name: "Create revised editorial draft",
      exact: true,
    });
    const revisedQuestion = `${item.prompt} Wählen Sie eine Antwort.`;
    await form
      .getByRole("textbox", { name: "Revised question", exact: true })
      .fill(revisedQuestion);
    const successor = await clickResponse(
      page,
      "Create revised editorial draft",
      `${collections}/${collection.id}/items/${item.item_version_id}/successor`,
      "POST",
      201,
    );
    expect(successor).toMatchObject({
      item_id: item.item_id,
      prompt: revisedQuestion,
      maturity: "AI_ASSISTED_DRAFT",
      review_receipts: [],
    });
    expect(successor.item_version_id).not.toBe(item.item_version_id);
    expect(successor.checksum).not.toBe(item.checksum);
    await expect(
      page.getByRole("article", { name: "Exact editorial item" }),
    ).toContainText(revisedQuestion);

    const historical = apiResponse(
      page,
      `${collections}/${collection.id}/items/${item.item_version_id}`,
    );
    await page
      .getByRole("button", {
        name: `Inspect ${item.version_label}`,
        exact: true,
      })
      .click();
    expect(await checked(await historical, 200)).toMatchObject({
      item_version_id: item.item_version_id,
      checksum: item.checksum,
      provenance_checksum: item.provenance_checksum,
      prompt: item.prompt,
      review_receipts: rejected.review_receipts,
    });
    await expect(
      page.getByRole("region", { name: "Immutable editorial review receipts" }),
    ).toContainText("TEST ONLY");
    await logout(page);
  });

  await test.step("Candidate cannot see the unpublished collection or keys", async () => {
    const candidate = await login(page, candidateEmail);
    const candidateHeaders = scoped(candidate, organisationId);
    const assignments = await checked(
      await page.request.get("/api/v1/candidate/exam-assignments", {
        headers: candidateHeaders,
      }),
      200,
    );
    expect(JSON.stringify(assignments)).not.toContain(collection.pack.id);
    for (const path of [
      `${collections}/${collection.id}`,
      `${collections}/${collection.id}/items/${item.item_version_id}`,
    ]) {
      const denied = await page.request.get(path, {
        headers: candidateHeaders,
      });
      expect([403, 404]).toContain(denied.status());
      expect(await denied.text()).not.toContain(item.prompt);
    }
    const deniedPreview = await page.request.post(
      `${collections}/${collection.id}/preview`,
      {
        headers: candidateHeaders,
        data: {
          expected_collection_checksum: collection.checksum,
          bundesland: "DE-BE",
        },
      },
    );
    expect([403, 404]).toContain(deniedPreview.status());
    expect(await deniedPreview.text()).not.toContain(item.prompt);
    await page.goto(workbench);
    await expect(
      page.getByRole("heading", { name: "Access denied", exact: true }),
    ).toBeVisible();
  });
});
