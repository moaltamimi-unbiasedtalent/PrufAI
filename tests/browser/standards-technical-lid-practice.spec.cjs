const { execFileSync } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { test, expect } = require("@playwright/test");
const { actor, clickResponse, headers } = require("./evaluation-fixture.cjs");
const {
  apiResponse,
  login,
  logout,
  restartOwnedApi,
  screenshot,
} = require("./support.cjs");

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

  await test.step("Control Centre distinguishes the installed TEST ONLY fixture from the draft", async () => {
    await openLid(page, platformEmail);
    const detail = await clickResponse(
      page,
      `Inspect LiD version ${draft.version_label}`,
      `${lid}/blueprints/${draft.id}`,
      "GET",
      200,
    );
    expect(detail).toMatchObject({
      status: "DRAFT",
      official_provider_authorization: false,
      synthetic_fixture_jurisdictions: [{ code: "DE-BE", name: "Berlin" }],
    });
    await expect(
      page.getByRole("region", { name: "LiD practice form" }),
    ).toContainText("TEST ONLY practice fixture coverage: Berlin (DE-BE)");
    const sourcePicker = page.getByRole("combobox", {
      name: "LiD source",
      exact: true,
    });
    await sourcePicker.selectOption(interactive);
    for (const width of [320, 375, 768, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBe(width);
      const button = page.getByRole("button", {
        name: "Inspect exact LiD source version",
      });
      await sourcePicker.focus();
      await page.keyboard.press("Tab");
      await expect(
        page.getByRole("button", {
          name: "Refresh exact sources and versions",
        }),
      ).toBeFocused();
      expect(
        await button.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }),
      ).toBe(true);
    }
    await logout(page);
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
    const changeState = await page.request.post(
      `${candidate}/exam-assignments/${assignmentId}/attempts`,
      { headers: scoped(owner, orgId), data: { bundesland: "DE-BY" } },
    );
    expect(changeState.status()).toBe(400);
    expect(await changeState.json()).toMatchObject({
      detail: "CONTENT_UNAVAILABLE",
    });
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

    // Control the display clock while intentionally dropping heartbeat requests.
    // A normal reload restores the deadline from the real server afterwards.
    await page.route("**/heartbeat", (route) => route.abort());
    await page.clock.install();
    const clock = page.locator("time[datetime]");
    const seconds = Number(
      (await clock.getAttribute("datetime")).match(/^PT(\d+)S$/)[1],
    );
    await page.clock.fastForward((seconds - 304) * 1000);
    await expect(clock).toHaveAttribute("datetime", /PT30[1-5]S/);
    await page.clock.fastForward(7000);
    const live = page
      .getByRole("region", { name: "Time remaining" })
      .locator("[aria-live=polite]");
    await expect(live).toHaveText("5 minutes remaining");
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(live).toHaveText("5 minutes remaining");
    await page.unroute("**/heartbeat");
    await page.reload();
    await expect(clock).toHaveAttribute("datetime", /PT(35\d\d|3600)S/);

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
        await restartOwnedApi();
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
    const trigger = page.getByRole("button", {
      name: "Submit attempt",
      exact: true,
    });
    await trigger.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", {
      name: "Submit this practice exam?",
    });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleDescription(
      /you can no longer change your answers/,
    );
    await expect(dialog).toContainText("0 of 33 questions unanswered");
    await expect(
      dialog.getByRole("button", { name: "Keep working" }),
    ).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(
      dialog.getByRole("button", { name: "Submit final answers" }),
    ).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(
      dialog.getByRole("button", { name: "Keep working" }),
    ).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await expect(page.getByRole("radio").first()).toBeEnabled();
    await trigger.click();
    const submitted = await clickResponse(
      page,
      "Submit final answers",
      `${candidate}/exam-attempts/${attemptId}/submit`,
      "POST",
      200,
    );
    expect(submitted).toMatchObject({ id: attemptId, status: "SUBMITTED" });
    await expect(
      page.getByText("Autosave: Submitted", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        /The practice result will appear after your institution reviews and releases it/,
      ),
    ).toBeVisible();
    await expect(page.getByRole("radio").first()).toBeDisabled();
    const unreleased = await page.request.get(`${candidate}/releases`, {
      headers: scoped(owner, orgId),
    });
    expect(unreleased.status()).toBe(200);
    expect(
      (await unreleased.json()).some(
        (release) => release.attempt_id === attemptId,
      ),
    ).toBe(false);
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
    const candidatePage = await page.context().browser().newPage({
      baseURL: process.env.PRUFAI_E2E_BASE_URL,
    });
    try {
      await login(candidatePage, candidateEmail);
      const pending = apiResponse(
        candidatePage,
        `/exam-attempts/${attemptId}/lid-practice-result`,
      );
      await candidatePage
        .getByRole("article", { name: title })
        .getByRole("link", { name: /View submitted state/ })
        .click();
      expect((await pending).status()).toBe(404);
      const result = candidatePage.getByRole("region", {
        name: "LiD practice result",
        exact: true,
      });
      await expect(result).toContainText(
        "The practice result will appear after your institution reviews and releases it.",
      );
      await expect(result).not.toContainText("answers correct");
      await logout(candidatePage);
    } finally {
      await candidatePage.close();
    }
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
    const owner = await login(page, candidateEmail);
    const released = await page.request.get(
      `${candidate}/releases/${releaseId}`,
      {
        headers: scoped(owner, orgId),
      },
    );
    expect(released.status()).toBe(200);
    expect(await released.json()).toMatchObject({
      attempt_id: attemptId,
      classification: "PRACTICE",
    });
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
    await expect(section).toContainText("Practice jurisdiction: Berlin");
    await expect(section).toContainText("Classification: PRACTICE");
    await expect(section).toContainText(
      "Administrative recognition: Not established.",
    );
    await expect(section).toContainText("15/33 · Numerical threshold met");
    await expect(section).toContainText("17/33 · Below numerical threshold");
    await expect(section).toContainText("NOT OFFICIAL · NOT CERTIFIED");
    await screenshot(page, "s3-lid-candidate-practice-result.png");
  });

  await test.step("revocation removes the candidate score and a separate expired attempt remains read-only", async () => {
    await logout(page);
    const admin = await login(page, adminEmail);
    const adminRoot = `/api/v1/organisations/${orgId}`;
    const revoke = await page.request.post(
      `${adminRoot}/releases/${releaseId}/revoke`,
      {
        headers: scoped(admin, orgId),
        data: {
          operation_id: randomUUID(),
          reason: "TEST ONLY LiD revocation proof",
        },
      },
    );
    expect(revoke.status()).toBe(200);
    const ownerProfile = await page.request.get(
      `${adminRoot}/exam-assignments`,
      {
        headers: scoped(admin, orgId),
      },
    );
    expect(ownerProfile.status()).toBe(200);
    const ownerId = (await ownerProfile.json()).find(
      (row) => row.id === assignmentId,
    ).candidate_user_id;
    const added = await page.request.post(`${adminRoot}/exam-assignments`, {
      headers: scoped(admin, orgId),
      data: {
        candidate_user_id: ownerId,
        exam_version_id: fixture.exam_version_id,
      },
    });
    expect(added.status()).toBe(201);
    const expiryAssignment = (await added.json()).id;
    await logout(page);
    const owner = await login(page, candidateEmail);
    await page.goto(`/candidate/exams/${attemptId}`);
    await expect(
      page.getByRole("status").filter({ hasText: /reviews and releases/ }),
    ).toBeVisible();
    await expect(page.getByText("15 of 33 answers correct")).toHaveCount(0);
    const withheld = await page.request.get(
      `${candidate}/releases/${releaseId}`,
      {
        headers: scoped(owner, orgId),
      },
    );
    expect(withheld.status()).toBe(200);
    expect(await withheld.json()).not.toHaveProperty("result");

    await page.goto(`/candidate/exams/assignment-${expiryAssignment}`);
    await page
      .getByRole("combobox", { name: "Practice Bundesland" })
      .selectOption("DE-BE");
    const prepared = await clickResponse(
      page,
      "Prepare exam",
      `${candidate}/exam-assignments/${expiryAssignment}/attempts`,
      "POST",
      201,
    );
    const started = await clickResponse(
      page,
      "Start exam",
      `${candidate}/exam-attempts/${prepared.id}/start`,
      "POST",
      200,
    );
    const first = started.runtime_plan.modules[0].sections[0].tasks[0].items[0];
    const saved = apiResponse(page, `/responses/${first.binding_id}`, "PUT");
    await page.getByRole("radio").first().check();
    expect((await saved).status()).toBe(200);
    execFileSync(
      "python3",
      ["tests/browser/expire-lid-attempt.py", prepared.id],
      { timeout: 30000 },
    );
    const denied = apiResponse(page, "/navigate", "POST");
    await page.getByRole("button", { name: "Next question" }).click();
    expect((await denied).status()).toBe(409);
    for (const afterReload of [false, true]) {
      if (afterReload) await page.reload();
      const notice = page.getByRole("region", {
        name: "Prüfungszeit abgelaufen",
      });
      await expect(notice).toHaveAttribute("lang", "de");
      await expect(notice.getByRole("alert")).toContainText("EXPIRED");
      await expect(
        notice.getByRole("link", { name: "Zurück zu meinen Prüfungen" }),
      ).toBeVisible();
      await expect(page.getByRole("radio").first()).toBeChecked();
      await expect(page.getByRole("radio").first()).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "Submit attempt" }),
      ).toBeDisabled();
    }
  });
});
