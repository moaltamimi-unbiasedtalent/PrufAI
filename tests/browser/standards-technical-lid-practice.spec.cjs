const { execFileSync } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { test, expect } = require("@playwright/test");
const { actor, clickResponse, headers } = require("./evaluation-fixture.cjs");
const { apiResponse, login, logout, screenshot } = require("./support.cjs");

const standards = "/api/v1/platform/standards";
const lid = `${standards}/lid`;
const packs = "/api/v1/platform/standard-pack-versions";
const candidate = "/api/v1/candidate";
const platformEmail = "platform.admin@demo.prufai.local";
const adminEmail = "admin@demo.prufai.local";
const candidateEmail = "candidate@demo.prufai.local";
const title = "Leben in Deutschland — Practice";
const interactive = "bamf-lid-interactive";

async function openLid(page, email) {
  const identity = await login(page, email);
  await page
    .getByRole("link", { name: "Standards & packs", exact: true })
    .click();
  const sources = apiResponse(page, `${lid}/sources`);
  await page
    .getByRole("link", { name: "Leben in Deutschland blueprint", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Leben in Deutschland blueprint",
      exact: true,
    }),
  ).toBeVisible();
  const response = await sources;
  expect(response.status()).toBe(200);
  return { identity, sources: await response.json() };
}

function scoped(identity, orgId) {
  return { ...headers(identity), "X-PrufAI-Organisation-ID": orgId };
}

function demoOrganisation(identity) {
  const membership = identity.profile.memberships.find(
    (item) => item.organisation_slug === "prufai-demo-academy-v2",
  );
  expect(membership?.organisation_id).toBeTruthy();
  return membership.organisation_id;
}

function expectSplit(preview) {
  expect(preview).toMatchObject({
    official_outcome: "UNDETERMINED",
    administrative_recognition: "NOT_ESTABLISHED",
  });
  expect(preview.contexts).toHaveLength(2);
  expect(preview.contexts).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        purpose: "ORIENTATION_COURSE_CONTEXT",
        threshold: 15,
        numerical_outcome: "THRESHOLD_MET",
      }),
      expect.objectContaining({
        purpose: "NATURALIZATION_KNOWLEDGE_CONTEXT",
        threshold: 17,
        numerical_outcome: "BELOW_THRESHOLD",
      }),
    ]),
  );
  for (const context of preview.contexts) {
    expect(context.rule_version_id).toBeTruthy();
    expect(context.rule_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(context.legal_effective_from).toBeTruthy();
    expect(context.citations.length).toBeGreaterThan(0);
    expect(
      context.citations.every(
        (citation) =>
          citation.source_version_id &&
          /^[a-f0-9]{64}$/.test(citation.source_metadata_checksum),
      ),
    ).toBe(true);
  }
}

test("LiD Standards, institution and candidate run the real Berlin practice journey with separate thresholds", async ({
  page,
}) => {
  let draft;
  let fixture;
  let orgId;
  let assignmentId;
  let attemptId;
  let scoringRun;
  let resultVersion;
  let releaseId;

  await test.step("record exact bibliographic sources and independent rights decisions", async () => {
    const { identity: author, sources } = await openLid(page, platformEmail);
    expect(sources.map((source) => source.key).sort()).toEqual([
      "bamf-lid-catalogue",
      interactive,
      "einbtestv-2024-211",
      "inttestv-2026-306",
      "intv-2026-218",
    ]);
    await page
      .getByRole("combobox", { name: "LiD source", exact: true })
      .selectOption(interactive);
    const metadata = page.getByRole("article", { name: "LiD source metadata" });
    await expect(
      metadata.getByRole("link", { name: "Official source" }),
    ).toHaveAttribute("href", /^https:\/\/oet\.bamf\.de\//);
    const selected = sources.find((source) => source.key === interactive);
    if (!selected.registered) {
      const created = await clickResponse(
        page,
        "Register LiD source metadata",
        `${lid}/sources/${interactive}/register`,
        "POST",
        200,
      );
      selected.registered = created.registered;
    }
    for (const source of sources.filter((row) => !row.registered)) {
      const response = await page.request.post(
        `${lid}/sources/${source.key}/register`,
        {
          headers: headers(author),
        },
      );
      expect(response.status(), source.key).toBe(200);
      source.registered = (await response.json()).registered;
    }
    await logout(page);

    const reviewer = await login(page, actor("reviewer"));
    expect(reviewer.profile.id).not.toBe(author.profile.id);
    for (const source of sources.filter((row) => !row.registered.reviewed)) {
      const response = await page.request.post(
        `${standards}/versions/${source.registered.id}/rights-review`,
        {
          headers: headers(reviewer),
          data: {
            expected_metadata_checksum: source.registered.metadata_checksum,
            decision: "METADATA_ONLY",
            rights_holder: source.rights_holder,
            licence_type: "REFERENCE_ONLY",
            basis_reference: "s3:official-source-metadata-only",
            retention_policy: "s3:source-catalogue-retention",
          },
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
  });

  await test.step("inspect persisted LiD claims, state mapping and numerical previews; deny draft publication", async () => {
    const { sources } = await openLid(page, platformEmail);
    expect(sources.every((source) => source.registered?.reviewed)).toBe(true);
    const selected = sources.find((source) => source.key === interactive);
    await page
      .getByRole("combobox", { name: "LiD source", exact: true })
      .selectOption(interactive);
    const exact = await clickResponse(
      page,
      "Inspect exact LiD source version",
      `${standards}/versions/${selected.registered.id}`,
      "GET",
      200,
    );
    expect(exact).toMatchObject({
      id: selected.registered.id,
      metadata_checksum: selected.registered.metadata_checksum,
      content_mode: "METADATA_ONLY",
      usage_rights: "BIBLIOGRAPHIC_REFERENCE_ONLY",
      sha256: null,
    });
    await expect(
      page.getByRole("region", { name: "Exact LiD source rights" }),
    ).toContainText("BIBLIOGRAPHIC_REFERENCE_ONLY");

    draft = await clickResponse(
      page,
      "Create or inspect LiD draft",
      `${lid}/blueprints`,
      "POST",
      201,
    );
    expect(draft).toMatchObject({
      status: "DRAFT",
      assessment_family: "INTEGRATION_KNOWLEDGE",
      exam_family: "LEBEN_IN_DEUTSCHLAND",
      language: "de",
      cefr_level: null,
      allowed_classifications: ["PRACTICE"],
      structure: {
        general_items: 30,
        state_items: 3,
        total_items: 33,
        options_per_item: 4,
        time_limit_seconds: 3600,
      },
    });
    expect(draft.jurisdictions).toHaveLength(16);
    expect(draft.claim_ledger.length).toBeGreaterThan(5);
    await expect(
      page.getByRole("region", { name: "LiD practice form" }),
    ).toContainText(/33 questions.*30 general.*3 selected-state/);
    await page.getByText("Bundesland mapping (16)", { exact: true }).click();
    await expect(
      page.getByRole("list", { name: "Bundesland codes" }),
    ).toContainText("DE-BE");
    const claims = page.getByRole("region", { name: "LiD legal claim ledger" });
    await claims
      .locator("summary")
      .filter({ hasText: /LID_ORIENTATION.*THRESHOLD/ })
      .click();
    await expect(
      claims.getByRole("link", { name: /§ 10/ }).first(),
    ).toBeVisible();
    await page
      .getByLabel("Synthetic correct count", { exact: true })
      .fill("15");
    await page
      .getByLabel("Legal as-of date", { exact: true })
      .fill("2026-11-06");
    const preview = await clickResponse(
      page,
      "Run LiD numerical preview",
      `${lid}/blueprints/${draft.id}/preview`,
      "POST",
      200,
    );
    expectSplit(preview);
    const visible = page.getByRole("status", {
      name: "LiD numerical preview result",
    });
    await expect(visible).toContainText(/15\/33.*Numerical threshold met/);
    await expect(visible).toContainText(/17\/33.*Below numerical threshold/);

    const pack = await clickResponse(
      page,
      "Open exact LiD Standard Pack version",
      `${packs}/${draft.id}`,
      "GET",
      200,
    );
    expect(pack).toMatchObject({
      id: draft.id,
      status: "DRAFT",
      checksum: draft.checksum,
    });
    const denied = await clickResponse(
      page,
      "Request LiD pack publication",
      `${packs}/${draft.id}/publish`,
      "POST",
      409,
    );
    expect(denied.detail).toBe("LID_DRAFT_PUBLICATION_BLOCKED");
    await expect(
      page
        .getByRole("article", { name: "Exact LiD blueprint version" })
        .getByRole("alert"),
    ).toHaveText("LID_DRAFT_PUBLICATION_BLOCKED");
    await expect(
      page.getByRole("region", { name: "LiD publication blockers" }),
    ).toContainText("LID_EXPERT_VALIDATION_REQUIRED");
    await screenshot(page, "s3-lid-draft-rules-and-publication-denial.png");
    const history = await clickResponse(
      page,
      `Inspect LiD version ${draft.version_label}`,
      `${lid}/blueprints/${draft.id}`,
      "GET",
      200,
    );
    expect(history).toMatchObject({
      id: draft.id,
      status: "DRAFT",
      checksum: draft.checksum,
    });
    await logout(page);

    // The CLI verifies the same explicitly disposable PostgreSQL target as the
    // guarded browser runner. It seeds test questions and a deliverable
    // practice exam through real services, after the governed draft exists.
    const output = execFileSync(
      "python3",
      [
        "-m",
        "app.scripts.seed_lid_practice",
        "--blueprint-version-id",
        draft.id,
      ],
      { encoding: "utf8", timeout: 60_000 },
    );
    fixture = JSON.parse(output.trim().split("\n").at(-1));
    expect(fixture).toMatchObject({
      exam_version_id: expect.any(String),
      bundesland: "DE-BE",
    });
  });

  await test.step("institution assigns an eligible synthetic practice version", async () => {
    const admin = await login(page, adminEmail);
    orgId = demoOrganisation(admin);
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    const candidateOption = page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .locator("option")
      .filter({ hasText: /^Demo Candidate$/ });
    await expect(candidateOption).toHaveCount(1);
    await page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .selectOption(await candidateOption.getAttribute("value"));
    await page
      .getByRole("combobox", { name: "Practice exam version", exact: true })
      .selectOption(fixture.exam_version_id);
    await expect(
      page.getByText(/The candidate selects the practice Bundesland/),
    ).toBeVisible();
    const created = await clickResponse(
      page,
      "Assign assessment",
      `/api/v1/organisations/${orgId}/exam-assignments`,
      "POST",
      201,
    );
    assignmentId = created.id;
    await logout(page);
  });

  await test.step("candidate explicitly pins Berlin, resumes and submits an original 30 + 3 form", async () => {
    const owner = await login(page, candidateEmail);
    const assignment = page.getByRole("article", { name: title });
    await assignment.getByRole("link", { name: /Prepare exam/ }).click();
    const state = page.getByRole("combobox", {
      name: "Practice Bundesland",
      exact: true,
    });
    await expect(state.locator("option[value^='DE-']")).toHaveCount(16);
    await expect(state.locator("option[value='DE-BY']")).toHaveJSProperty(
      "disabled",
      true,
    );
    await expect(
      page.getByRole("button", { name: "Prepare exam", exact: true }),
    ).toBeDisabled();
    const unavailable = await page.request.post(
      `${candidate}/exam-assignments/${assignmentId}/attempts`,
      {
        headers: scoped(owner, orgId),
        data: { bundesland: "DE-BY" },
      },
    );
    expect(unavailable.status()).toBe(400);
    expect(await unavailable.json()).toMatchObject({
      detail: "CONTENT_UNAVAILABLE",
    });
    await state.selectOption("DE-BE");
    const prepared = await clickResponse(
      page,
      "Prepare exam",
      `${candidate}/exam-assignments/${assignmentId}/attempts`,
      "POST",
      201,
    );
    attemptId = prepared.id;
    await expect(page).toHaveURL(new RegExp(`/candidate/exams/${attemptId}$`));
    await expect(
      page.getByText(/Selected practice Bundesland: Berlin/),
    ).toBeVisible();
    await expect(
      page.getByText("Duration: 60 minutes", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("radio")).toHaveCount(0);
    const started = await clickResponse(
      page,
      "Start exam",
      `${candidate}/exam-attempts/${attemptId}/start`,
      "POST",
      200,
    );
    expect(started).toMatchObject({
      attempt_id: attemptId,
      status: "IN_PROGRESS",
      runtime_plan: {
        duration_seconds: 3600,
        lid_practice: {
          bundesland: "DE-BE",
          general_count: 30,
          state_count: 3,
          attempt_id: attemptId,
        },
      },
    });
    expect(
      Date.parse(started.deadline_at) - Date.parse(started.started_at),
    ).toBe(3_600_000);
    const items = started.runtime_plan.modules.flatMap((module) =>
      module.sections.flatMap((section) =>
        section.tasks.flatMap((task) => task.items),
      ),
    );
    expect(items).toHaveLength(33);
    expect(new Set(items.map((item) => item.item_version_id)).size).toBe(33);
    expect(
      items.filter((item) => item.lid_pool_scope === "GENERAL_DE"),
    ).toHaveLength(30);
    expect(
      items.filter((item) => item.lid_pool_scope === "BUNDESLAND_SPECIFIC"),
    ).toHaveLength(3);
    expect(JSON.stringify(started.runtime_plan)).not.toMatch(
      /answer_key|correct_option|scoring_key|legal_notes/,
    );
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByText(
          /ORIGINAL SYNTHETIC TEST CONTENT.*NOT AN OFFICIAL BAMF QUESTION/,
        )
        .first(),
    ).toBeVisible();

    for (const [index, item] of items.entries()) {
      await expect(
        page.getByText(`Current question ${index + 1}`, { exact: true }),
      ).toBeVisible();
      const radios = page.getByRole("radio");
      await expect(radios).toHaveCount(4);
      const fixtureNumber = Number(
        /^original-synthetic-(\d+)-v1$/.exec(item.version_label)?.[1],
      );
      expect(fixtureNumber).toBeGreaterThan(0);
      // The disposable fixture rotates the keyed position by original item
      // number. Use that external test contract, never a candidate API key.
      const optionNumber =
        ((fixtureNumber - 1 + (index >= 15 ? 1 : 0)) % 4) + 1;
      const choice = item.content.options.find(
        (option) => option.option_id === `option-${optionNumber}`,
      );
      expect(choice?.display_text).toBeTruthy();
      const saved = apiResponse(page, `/responses/${item.binding_id}`, "PUT");
      await page
        .getByRole("radio", { name: choice.display_text, exact: true })
        .check();
      expect((await saved).status()).toBe(200);
      await expect(
        page.getByText("Saved response revision: 1", { exact: true }),
      ).toBeVisible();
      if (index === 2) {
        await page.reload();
        await expect(
          page.getByText("Selected practice Bundesland: Berlin", {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByRole("combobox", {
            name: "Practice Bundesland",
            exact: true,
          }),
        ).toHaveCount(0);
        await expect(
          page.getByRole("radio", { name: choice.display_text, exact: true }),
        ).toBeChecked();
      }
      if (index < items.length - 1)
        await page
          .getByRole("button", { name: "Next question", exact: true })
          .click();
    }
    const submitted = await clickResponse(
      page,
      "Submit attempt",
      `${candidate}/exam-attempts/${attemptId}/submit`,
      "POST",
      200,
    );
    expect(submitted).toMatchObject({ id: attemptId, status: "SUBMITTED" });
    await expect(
      page.getByText("Autosave: Submitted", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/The practice result will appear after scoring/),
    ).toBeVisible();
    await expect(page.getByRole("radio").first()).toBeDisabled();
    await logout(page);
  });

  await test.step("canonical scorer persists the result and the exact practice release rejects certificate issuance", async () => {
    const admin = await login(page, adminEmail);
    const attempt = page.locator(`#attempt-${attemptId}`);
    const score = apiResponse(
      page,
      `/exam-attempts/${attemptId}/score`,
      "POST",
    );
    await attempt
      .getByRole("button", { name: "Score submitted attempt", exact: true })
      .click();
    const scoreResponse = await score;
    expect(scoreResponse.status()).toBe(200);
    scoringRun = await scoreResponse.json();
    expect(scoringRun.status).toBe("COMPLETED");
    await expect(
      attempt.getByText(/Raw score: 15(?:\.0+)? \/ 33(?:\.0+)?/),
    ).toBeVisible();
    await attempt
      .getByRole("link", {
        name: "Inspect score, review and release →",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/\/institution\/results\/[^/]+$/);
    const resultId = new URL(page.url()).pathname.split("/").at(-1);
    const versions = await page.request.get(
      `/api/v1/organisations/${orgId}/results/${resultId}/versions`,
      {
        headers: scoped(admin, orgId),
      },
    );
    expect(versions.status()).toBe(200);
    [resultVersion] = await versions.json();
    expect(resultVersion).toMatchObject({
      attempt_id: attemptId,
      scoring_run_id: scoringRun.id,
      outcome: "UNDETERMINED",
    });
    const versionRoute = `/api/v1/organisations/${orgId}/result-versions/${resultVersion.result_version_id}`;
    await clickResponse(
      page,
      "Request human result review",
      `${versionRoute}/request-review`,
      "POST",
      200,
    );
    await clickResponse(
      page,
      "Approve reviewed result",
      `${versionRoute}/approve`,
      "POST",
      200,
    );
    const released = await clickResponse(
      page,
      "Release approved result",
      `${versionRoute}/release`,
      "POST",
      200,
    );
    releaseId = released.release_id;
    expect(releaseId).toBeTruthy();
    const releaseRoute = `/api/v1/organisations/${orgId}/releases/${releaseId}`;
    const denied = await page.request.post(
      `${releaseRoute}/certificate?authorization_id=${randomUUID()}`,
      {
        headers: scoped(admin, orgId),
        data: { operation_id: randomUUID() },
      },
    );
    expect(denied.status()).toBe(409);
    expect(await denied.json()).toMatchObject({
      detail: "CERTIFICATION.NOT_AUTHORIZED",
    });
    const release = await page.request.get(releaseRoute, {
      headers: scoped(admin, orgId),
    });
    expect(release.status()).toBe(200);
    expect(await release.json()).toMatchObject({
      id: releaseId,
      attempt_id: attemptId,
      classification: "PRACTICE",
      certificates: [],
    });
    await logout(page);

    const operator = await login(page, platformEmail);
    const prohibited = await page.request.post(
      `/api/v1/organisations/${orgId}/certification-authorizations`,
      {
        headers: scoped(operator, orgId),
        data: {
          assessment_scope: "OFFICIAL_CERTIFICATION",
          provider_scope: draft.code,
          standard_pack_version_id: draft.id,
          evidence_reference: "synthetic:unauthorized-lid-browser-negative",
          effective_from: "2026-11-06T00:00:00Z",
          operation_id: randomUUID(),
        },
      },
    );
    expect(prohibited.status()).toBe(409);
    expect(await prohibited.json()).toMatchObject({
      detail: "CERTIFICATION.NOT_AUTHORIZED",
    });
    await logout(page);
  });

  await test.step("candidate sees exact 15/33 practice evidence without an official outcome or credential", async () => {
    await login(page, candidateEmail);
    const preview = apiResponse(
      page,
      `/exam-attempts/${attemptId}/lid-practice-result`,
    );
    await page
      .getByRole("article", { name: title })
      .getByRole("link", { name: /View submitted state/ })
      .click();
    const response = await preview;
    expect(response.status()).toBe(200);
    const result = await response.json();
    expect(result).toMatchObject({
      raw_correct: 15,
      total_items: 33,
      scoring_run_id: scoringRun.id,
      result_version_id: resultVersion.result_version_id,
      certification: "NOT_CERTIFIED",
    });
    expectSplit(result);
    expect(JSON.stringify(result)).not.toMatch(
      /rights_decision|rights_status|rights_review|private_source|answer_key/,
    );
    const section = page.getByRole("region", {
      name: "LiD practice result",
      exact: true,
    });
    await expect(section).toContainText("15 of 33 answers correct");
    await expect(section).toContainText("15/33 · Numerical threshold met");
    await expect(section).toContainText("17/33 · Below numerical threshold");
    await expect(section).toContainText("NOT OFFICIAL · NOT CERTIFIED");
    await screenshot(page, "s3-lid-candidate-practice-result.png");
  });
});
