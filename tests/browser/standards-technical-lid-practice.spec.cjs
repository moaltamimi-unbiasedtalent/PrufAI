const { execFileSync } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { test, expect } = require("@playwright/test");
const { actor, clickResponse, headers } = require("./evaluation-fixture.cjs");
const {
  apiResponse,
  login,
  logout,
  restartOwnedApi,
} = require("./support.cjs");

test.use({ trace: "off", screenshot: "off", video: "off" });

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

async function privateRequest(page, method, path, options) {
  try {
    return await page.request.fetch(path, {
      ...options,
      method,
      failOnStatusCode: false,
      timeout: 10000,
    });
  } catch {
    throw new Error("The LiD API probe did not return an HTTP status.");
  }
}

async function callUpstream(route) {
  try {
    return await route.fetch({ timeout: 6000 });
  } catch {
    await route.abort("failed");
    throw new Error("The intercepted LiD write did not reach the test server.");
  }
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
      const response = await privateRequest(
        page,
        "POST",
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
      const response = await privateRequest(
        page,
        "POST",
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
    const unavailable = await privateRequest(
      page,
      "POST",
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
    const changeState = await privateRequest(
      page,
      "POST",
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
    const unreleased = await privateRequest(
      page,
      "GET",
      `${candidate}/releases`,
      {
        headers: scoped(owner, orgId),
      },
    );
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
    const versions = await privateRequest(
      page,
      "GET",
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
    const denied = await privateRequest(
      page,
      "POST",
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
    const release = await privateRequest(page, "GET", releaseRoute, {
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
    const prohibited = await privateRequest(
      page,
      "POST",
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
    const released = await privateRequest(
      page,
      "GET",
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
  });

  await test.step("revocation removes the candidate score and a separate expired attempt remains read-only", async () => {
    await logout(page);
    const admin = await login(page, adminEmail);
    const adminRoot = `/api/v1/organisations/${orgId}`;
    const revoke = await privateRequest(
      page,
      "POST",
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
    const ownerProfile = await privateRequest(
      page,
      "GET",
      `${adminRoot}/exam-assignments`,
      {
        headers: scoped(admin, orgId),
      },
    );
    expect(ownerProfile.status()).toBe(200);
    const ownerId = (await ownerProfile.json()).find(
      (row) => row.id === assignmentId,
    ).candidate_user_id;
    const added = await privateRequest(
      page,
      "POST",
      `${adminRoot}/exam-assignments`,
      {
        headers: scoped(admin, orgId),
        data: {
          candidate_user_id: ownerId,
          exam_version_id: fixture.exam_version_id,
        },
      },
    );
    expect(added.status()).toBe(201);
    const expiryAssignment = (await added.json()).id;
    await logout(page);
    const owner = await login(page, candidateEmail);
    await page.goto(`/candidate/exams/${attemptId}`);
    await expect(
      page.getByRole("status").filter({ hasText: /reviews and releases/ }),
    ).toBeVisible();
    await expect(page.getByText("15 of 33 answers correct")).toHaveCount(0);
    const withheld = await privateRequest(
      page,
      "GET",
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

async function createPracticeAssignment(page) {
  const operator = await login(page, adminEmail);
  const org = demoOrganisation(operator);
  const collection = `/api/v1/organisations/${org}/exam-assignments`;
  const listed = await privateRequest(page, "GET", collection, {
    headers: scoped(operator, org),
  });
  expect(listed.status()).toBe(200);
  const rows = await listed.json();
  const existing = rows.find(
    (row) =>
      row.exam_name === title &&
      row.exam_version_label === "original-synthetic-v1" &&
      row.candidate_name === "Demo Candidate",
  );
  let candidateId = existing?.candidate_user_id;
  let versionId = existing?.exam_version_id;
  if (!versionId) {
    const output = execFileSync(
      "python3",
      ["-m", "app.scripts.seed_lid_practice", "--prepare-demo"],
      {
        env: {
          ...process.env,
          PRUFAI_DEMO_PASSWORD:
            process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!",
        },
        timeout: 30_000,
      },
    )
      .toString()
      .trim()
      .split("\n")
      .at(-1);
    const fixture = JSON.parse(output);
    versionId = fixture.exam_version_id;
    const created = await privateRequest(page, "GET", collection, {
      headers: scoped(operator, org),
    });
    expect(created.status()).toBe(200);
    candidateId = (await created.json()).find(
      (row) => row.id === fixture.candidate_assignment_id,
    )?.candidate_user_id;
  }
  expect(Boolean(candidateId && versionId)).toBe(true);
  const assigned = await privateRequest(page, "POST", collection, {
    headers: scoped(operator, org),
    data: { candidate_user_id: candidateId, exam_version_id: versionId },
  });
  expect(assigned.status()).toBe(201);
  const { id } = await assigned.json();
  await logout(page);
  return { org, id };
}

async function tabTo(page, target, max = 32) {
  const focused = () =>
    target.evaluate((element) => document.activeElement === element);
  for (let step = 0; step < max && !(await focused()); step += 1)
    await page.keyboard.press("Tab");
  await expect(target).toBeFocused();
}

async function keyboardCandidateLogin(page) {
  await page.goto("/anmelden");
  const email = page.getByLabel("E-Mail-Adresse", { exact: true });
  const password = page.getByLabel("Passwort", { exact: true });
  await expect(email).toBeEnabled();
  await tabTo(page, email);
  await page.keyboard.insertText(candidateEmail);
  await page.keyboard.press("Tab");
  await expect(password).toBeFocused();
  // The field is filled through the keyboard. The private project keeps form
  // snapshots out of uploads, and insertText errors must never print the value.
  try {
    await page.keyboard.insertText(
      process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!",
    );
  } catch {
    throw new Error("The private sign-in field could not be filled.");
  }
  const response = apiResponse(page, "/api/v1/auth/login", "POST");
  await page.keyboard.press("Enter");
  const reply = await response;
  expect(reply.status()).toBe(200);
  const { readSignInToken } = require("./enrollment-probes.cjs");
  const token = await readSignInToken(reply);
  await expect(page).toHaveURL(/\/candidate$/);
  return { token };
}

async function startFromKeyboard(page, id, nearDeadline) {
  await page.goto(`/candidate/exams/assignment-${id}`);
  const state = page.getByRole("combobox", { name: "Practice Bundesland" });
  await tabTo(page, state);
  await page.keyboard.press("b");
  await expect(state).toHaveValue("DE-BE");
  await tabTo(page, page.getByRole("button", { name: "Prepare exam" }));
  const created = apiResponse(page, `/exam-assignments/${id}/attempts`, "POST");
  await page.keyboard.press("Enter");
  const prepared = await created;
  expect(prepared.status()).toBe(201);
  const {
    id: attempt,
    started_at: startedAt,
    deadline_at: deadlineAt,
  } = await prepared.json();
  expect(startedAt).toBeNull();
  expect(deadlineAt).toBeNull();
  await expect(page.locator("time")).toHaveCount(0);
  await expect(page.getByRole("radio")).toHaveCount(0);
  if (nearDeadline) {
    execFileSync(
      "python3",
      ["tests/browser/expire-lid-attempt.py", attempt, String(nearDeadline)],
      { timeout: 30_000 },
    );
    await page.reload();
    return { attempt_id: attempt };
  }
  await tabTo(page, page.getByRole("button", { name: "Start exam" }));
  const started = apiResponse(page, `/exam-attempts/${attempt}/start`, "POST");
  await page.keyboard.press("Enter");
  const reply = await started;
  expect(reply.status()).toBe(200);
  const runtime = await reply.json();
  expect(Date.parse(runtime.deadline_at) - Date.parse(runtime.started_at)).toBe(
    3600000,
  );
  await expect(
    page.getByRole("heading", { name: "Question 1 of 33" }),
  ).toBeFocused();
  return runtime;
}

test("narrow keyboard Berlin practice recovers lost acknowledgements and resolves a stale revision safely", async ({
  page,
  context,
}) => {
  const { id, org } = await createPracticeAssignment(page);
  await page.setViewportSize({ width: 320, height: 830 });
  await expect(page.locator("body")).toBeVisible();
  const owner = await keyboardCandidateLogin(page);
  const runtime = await startFromKeyboard(page, id);
  const attempt = runtime.attempt_id;
  const root = `${candidate}/exam-attempts/${attempt}`;
  const [first, second] =
    runtime.runtime_plan.modules[0].sections[0].tasks[0].items;
  const mobileWidth = () =>
    page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  expect(await mobileWidth()).toBe(true);
  const answers = page.getByRole("radio");
  const retry = page.getByRole("button", {
    name: "Speicherung erneut versuchen",
  });
  const revision = (value) =>
    page.getByText(`Saved response revision: ${value}`, { exact: true });
  const saved = () =>
    expect(
      page
        .getByRole("status")
        .filter({ hasText: "Alle Änderungen sind vom Server bestätigt." }),
    ).toBeVisible();
  await saved();
  const path = `**/responses/${first.binding_id}`;
  let sentId;
  let retryId;
  let held;
  const committed = new Promise((resolve) => {
    held = resolve;
  });
  await page.route(path, async (route) => {
    const operation = route.request().postDataJSON().client_operation_id;
    if (sentId) {
      retryId = operation;
      await route.continue();
      return;
    }
    sentId = operation;
    const response = await callUpstream(route);
    await route.abort("connectionfailed");
    held(response.status());
  });
  await tabTo(page, answers.first());
  await page.keyboard.press("Space");
  expect(await committed).toBe(200);
  await expect(retry).toBeEnabled();
  await expect(
    page.getByRole("status").filter({ hasText: "Speicherung nicht bestätigt" }),
  ).toBeVisible();
  await expect(revision(0)).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(new RegExp(attempt + "$"));
  await saved();
  expect(Boolean(retryId && retryId === sentId)).toBe(true);
  await expect(revision(1)).toBeVisible();
  await expect(answers.first()).toBeChecked();
  await page.unroute(path);

  // Drop before commit while navigator still reports online. An explicit retry
  // must send the same ID with the same revision and then update the UI.
  let droppedId;
  let sentAgain;
  await page.route(path, async (route) => {
    const operation = route.request().postDataJSON().client_operation_id;
    if (!droppedId) {
      droppedId = operation;
      await route.abort("connectionfailed");
    } else {
      sentAgain = operation;
      await route.continue();
    }
  });
  await answers.first().focus();
  await page.keyboard.press("ArrowDown");
  await expect(retry).toBeEnabled();
  await tabTo(page, retry);
  await page.keyboard.press("Enter");
  await saved();
  expect(Boolean(sentAgain && sentAgain === droppedId)).toBe(true);
  await expect(revision(2)).toBeVisible();
  await expect(answers.nth(1)).toBeChecked();
  await page.unroute(path);

  // Two tabs hold revision 2. Only the first writer may create revision 3.
  const secondTab = await context.newPage();
  await secondTab.goto(`/candidate/exams/${attempt}`);
  const tabAnswers = secondTab.getByRole("radio");
  await expect(tabAnswers.nth(1)).toBeChecked();
  await expect(secondTab.getByText("Saved response revision: 2")).toBeVisible();
  await page.bringToFront();
  await answers.nth(1).focus();
  await page.keyboard.press("ArrowDown");
  await expect(revision(3)).toBeVisible();
  await tabAnswers.nth(1).focus();
  await secondTab.keyboard.press("ArrowUp");
  const serverAnswer = secondTab.getByRole("button", {
    name: "Bestätigte Antwort laden",
  });
  await expect(serverAnswer).toBeEnabled();
  await expect(
    secondTab
      .getByRole("status")
      .filter({ hasText: "Eine neuere Antwort liegt auf dem Server" }),
  ).toBeVisible();
  await tabTo(secondTab, serverAnswer);
  await secondTab.keyboard.press("Enter");
  await expect(tabAnswers.nth(2)).toBeChecked();
  await expect(secondTab.getByText("Saved response revision: 3")).toBeVisible();
  await secondTab.close();
  await page.bringToFront();

  const trigger = page.getByRole("button", { name: "Submit attempt" });
  await expect(trigger).toBeEnabled();
  await tabTo(page, trigger);
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", {
    name: "Submit this practice exam?",
  });
  await expect(
    dialog.getByRole("button", { name: "Keep working" }),
  ).toBeFocused();
  expect(await mobileWidth()).toBe(true);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");
  await tabTo(
    page,
    dialog.getByRole("button", { name: "Erste unbeantwortete Frage" }),
  );
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Question 2 of 33" }),
  ).toBeFocused();

  // Queue a response while genuinely offline. No successful API response is mocked.
  await saved();
  await context.setOffline(true);
  await expect(
    page.getByRole("status").filter({ hasText: "Verbindung unterbrochen" }),
  ).toBeVisible();
  await tabTo(page, answers.first());
  await page.keyboard.press("Space");
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Offline. Diese Antwort wartet" }),
  ).toBeVisible();
  await expect(trigger).toBeDisabled();
  await context.setOffline(false);
  await expect(revision(1)).toBeVisible();
  await saved();
  await expect(page.getByRole("radio").first()).toBeChecked();
  await restartOwnedApi();
  await page.close();
  page = await context.newPage();
  await page.setViewportSize({ width: 320, height: 830 });
  await page.goto(`/candidate/exams/${attempt}`);
  await expect(
    page.getByRole("heading", { name: "Question 2 of 33" }),
  ).toBeVisible();
  await expect(page.getByRole("radio").first()).toBeChecked();

  // Withhold the acknowledgement after the real API commits, until the
  // candidate's bounded request times out. The subsequent GET must recover it.
  let releaseRoute;
  const waiting = new Promise((resolve) => {
    releaseRoute = resolve;
  });
  let posted;
  const submission = new Promise((resolve) => {
    posted = resolve;
  });
  await page.route("**/submit", async (route) => {
    const response = await callUpstream(route);
    posted(response.status());
    await waiting;
    await route.abort("timedout").catch(() => undefined);
  });
  try {
    await tabTo(page, page.getByRole("button", { name: "Submit attempt" }));
    await page.keyboard.press("Enter");
    await tabTo(
      page,
      page
        .getByRole("dialog")
        .getByRole("button", { name: "Submit final answers" }),
    );
    await page.keyboard.press("Enter");
    expect(await submission).toBe(200);
    const confirmation = page.getByRole("heading", {
      name: "Submission state: Submitted",
    });
    await expect(confirmation).toBeFocused({ timeout: 15_000 });
  } finally {
    releaseRoute();
    await page.unroute("**/submit");
  }
  const headers = scoped(owner, org);
  const repeated = await privateRequest(page, "POST", root + "/submit", {
    headers,
  });
  expect(repeated.status()).toBe(200);
  const submitted = await repeated.json();
  expect(submitted.status === "SUBMITTED" && submitted.id === attempt).toBe(
    true,
  );
  const ending = await privateRequest(page, "GET", root + "/runtime", {
    headers,
  });
  expect(ending.status()).toBe(200);
  const responses = (await ending.json()).responses;
  expect(responses.length).toBe(2);
  expect(
    responses.find((answer) => answer.binding_id === second.binding_id)
      ?.revision,
  ).toBe(1);
  const result = await privateRequest(
    page,
    "GET",
    root + "/lid-practice-result",
    { headers },
  );
  expect(result.status()).toBe(404);
  await expect(
    page.getByRole("status").filter({ hasText: "reviews and releases" }),
  ).toBeVisible();
  await tabTo(
    page,
    page.getByRole("link", { name: "Zurück zu meinen Prüfungen" }),
  );
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/candidate$/);
  const signOut = page.getByRole("button", { name: /Abmelden|Sign out/ });
  await tabTo(page, signOut);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/(anmelden|login)$/);
});

test("server deadline rejects a queued Berlin answer reconnected after time expires", async ({
  page,
  context,
}) => {
  const { id, org } = await createPracticeAssignment(page);
  const owner = await keyboardCandidateLogin(page);
  const { attempt_id: attempt } = await startFromKeyboard(page, id, 20);
  const clock = page.locator("time[datetime]");
  await expect(clock).toHaveAttribute("datetime", /^PT(?:[1-9]|1\d|20)S$/);
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Alle Änderungen sind vom Server bestätigt." }),
  ).toBeVisible();
  const root = `${candidate}/exam-attempts/${attempt}`;
  const current = await privateRequest(page, "GET", root + "/runtime", {
    headers: scoped(owner, org),
  });
  expect(current.status()).toBe(200);
  const runtime = await current.json();
  const first = runtime.runtime_plan.modules[0].sections[0].tasks[0].items[0];
  await context.setOffline(true);
  await tabTo(page, page.getByRole("radio").first());
  await page.keyboard.press("Space");
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Offline. Diese Antwort wartet" }),
  ).toBeVisible();
  // Let the actual persisted deadline pass while the browser is offline.
  // The offline timer must never turn an unconfirmed answer into a saved one.
  await expect(clock).toHaveAttribute("datetime", "PT0S", { timeout: 25_000 });
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Offline. Diese Antwort wartet" }),
  ).toBeVisible();
  const rejected = apiResponse(page, `/responses/${first.binding_id}`, "PUT");
  await context.setOffline(false);
  expect((await rejected).status()).toBe(409);
  await expect(
    page.getByRole("region", { name: "Prüfungszeit abgelaufen" }),
  ).toBeVisible();
  await expect(page.getByRole("radio").first()).not.toBeChecked();
  await expect(page.getByRole("radio").first()).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Submit attempt" }),
  ).toBeDisabled();
  const state = await privateRequest(page, "GET", root + "/runtime", {
    headers: scoped(owner, org),
  });
  expect(state.status()).toBe(200);
  const final = await state.json();
  expect(final.status).toBe("EXPIRED");
  expect(final.responses.length).toBe(0);

  // Prepare another attempt in the same private Berlin fixture and submit
  // before its actual deadline. The server records the final timestamp.
  await page.getByRole("link", { name: "Zurück zu meinen Prüfungen" }).click();
  await expect(page).toHaveURL(/\/candidate$/);
  const logoutResponse = apiResponse(page, "/api/v1/auth/logout", "POST");
  await page.getByRole("button", { name: /Abmelden|Sign out/ }).click();
  expect((await logoutResponse).status()).toBe(204);
  const next = await createPracticeAssignment(page);
  await keyboardCandidateLogin(page);
  const started = await startFromKeyboard(page, next.id, 20);
  await expect(clock).toHaveAttribute("datetime", /^PT(?:[1-9]|1\d|20)S$/);
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Alle Änderungen sind vom Server bestätigt." }),
  ).toBeVisible();
  const submit = apiResponse(
    page,
    `/exam-attempts/${started.attempt_id}/submit`,
    "POST",
  );
  await page.getByRole("button", { name: "Submit attempt" }).click();
  await page.getByRole("button", { name: "Submit final answers" }).click();
  const reply = await submit;
  expect(reply.status()).toBe(200);
  const confirmed = await reply.json();
  const timeLeft =
    Date.parse(confirmed.deadline_at) - Date.parse(confirmed.submitted_at);
  expect(timeLeft > 0 && timeLeft <= 20_000).toBe(true);
});

test("Berlin pilot restricts role workspaces and withdraws exact results despite stale responses", async ({
  page,
  browser,
}) => {
  const { org, id } = await createPracticeAssignment(page);
  const institution = `/api/v1/organisations/${org}`;
  const unrestricted = await login(page, adminEmail);
  expect(unrestricted.profile.v1_pilot_profile).toBe("DISABLED");
  const options = await privateRequest(
    page,
    "GET",
    `${institution}/exam-assignment-options`,
    {
      headers: scoped(unrestricted, org),
    },
  );
  expect(options.status()).toBe(200);
  const original = await options.json();
  const other = original.exam_versions.find((version) => !version.lid_practice);
  const candidateId = original.candidates.find(
    (row) => row.display_name === "Demo Candidate",
  )?.id;
  expect(Boolean(other && candidateId)).toBe(true);
  await logout(page);

  const operatorPage = await browser.newPage({
    baseURL: process.env.PRUFAI_E2E_BASE_URL,
  });
  const outsiderPage = await browser.newPage({
    baseURL: process.env.PRUFAI_E2E_BASE_URL,
  });
  const scorePage = await browser.newPage({
    baseURL: process.env.PRUFAI_E2E_BASE_URL,
  });
  try {
    await restartOwnedApi("BERLIN_LID_SYNTHETIC");
    const platform = await login(page, platformEmail);
    expect(platform.profile.v1_pilot_profile).toBe("BERLIN_LID_SYNTHETIC");
    await expect(
      page.getByRole("link", { name: "Berlin LiD blueprint", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "AI operations" })).toHaveCount(
      0,
    );
    await page
      .getByRole("link", { name: "Berlin LiD blueprint", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Leben in Deutschland blueprint" }),
    ).toBeVisible();
    await page.goto("/control-center/ai-operations");
    await expect(
      page.getByRole("heading", { name: "Access restricted" }),
    ).toBeVisible();
    const denied = await privateRequest(
      page,
      "POST",
      `/api/v1/platform/ai/workflow-versions/${randomUUID()}/run`,
      {
        headers: headers(platform),
        data: { request_id: randomUUID() },
      },
    );
    expect(denied.status()).toBe(403);
    expect(await denied.json()).toEqual({ detail: "PILOT_SCOPE_DENIED" });
    const classified = await privateRequest(
      page,
      "POST",
      `/api/v1/platform/exams/${other.exam_id}/versions`,
      {
        headers: scoped(platform, org),
        data: { classification: "OFFICIAL_CERTIFICATION" },
      },
    );
    expect(classified.status()).toBe(403);
    expect(await classified.json()).toEqual({ detail: "PILOT_SCOPE_DENIED" });
    await logout(page);

    const operator = await login(operatorPage, adminEmail);
    await expect(
      operatorPage.getByRole("link", { name: "Writing review" }),
    ).toHaveCount(0);
    await operatorPage.goto("/institution/speaking-reviews");
    await expect(
      operatorPage.getByRole("heading", { name: "Access restricted" }),
    ).toBeVisible();
    const forbidden = await privateRequest(
      operatorPage,
      "POST",
      `${institution}/exam-assignments`,
      {
        headers: scoped(operator, org),
        data: { candidate_user_id: candidateId, exam_version_id: other.id },
      },
    );
    expect(forbidden.status()).toBe(403);
    expect(await forbidden.json()).toEqual({ detail: "PILOT_SCOPE_DENIED" });
    const available = await privateRequest(
      operatorPage,
      "GET",
      `${institution}/exam-assignment-options`,
      { headers: scoped(operator, org) },
    );
    const offered = (await available.json()).exam_versions;
    expect(
      offered.length > 0 && offered.every((exam) => exam.lid_practice),
    ).toBe(true);

    const owner = await login(page, candidateEmail);
    const choice = await privateRequest(
      page,
      "POST",
      `${candidate}/exam-assignments/${id}/attempts`,
      {
        headers: scoped(owner, org),
        data: { bundesland: "DE-BY" },
      },
    );
    expect(choice.status()).toBe(403);
    expect(await choice.json()).toEqual({ detail: "PILOT_SCOPE_DENIED" });
    const runtime = await startFromKeyboard(page, id);
    const attempt = runtime.attempt_id;
    const attemptPath = `${candidate}/exam-attempts/${attempt}`;
    const first = runtime.runtime_plan.modules[0].sections[0].tasks[0].items[0];
    const audio = await privateRequest(
      page,
      "POST",
      `${candidate}/attempts/${attempt}/items/${first.binding_id}/audio-recordings`,
      { headers: scoped(owner, org) },
    );
    expect(audio.status()).toBe(403);
    expect(await audio.json()).toEqual({ detail: "PILOT_SCOPE_DENIED" });
    const answered = await privateRequest(
      page,
      "PUT",
      `${attemptPath}/responses/${first.binding_id}`,
      {
        headers: scoped(owner, org),
        data: {
          client_operation_id: randomUUID(),
          expected_revision: 0,
          payload: { option_id: first.content.options[0].option_id },
        },
      },
    );
    expect(answered.status()).toBe(200);
    expect(
      (
        await privateRequest(page, "POST", `${attemptPath}/submit`, {
          headers: scoped(owner, org),
        })
      ).status(),
    ).toBe(200);
    expect(
      (
        await privateRequest(
          operatorPage,
          "POST",
          `${institution}/exam-attempts/${attempt}/score`,
          { headers: scoped(operator, org) },
        )
      ).status(),
    ).toBe(200);
    const rows = await privateRequest(
      operatorPage,
      "GET",
      `${institution}/exam-assignments`,
      { headers: scoped(operator, org) },
    );
    const assignment = (await rows.json()).find((row) => row.id === id);
    const result = assignment.attempts.find((row) => row.id === attempt).result;
    const legacy = `${candidate}/results/${result.id}`;
    const releaseAction = `${institution}/result-versions/${result.version_id}`;
    const ownerRequest = (method, path, data) =>
      privateRequest(page, method, path, { headers: scoped(owner, org), data });
    const staff = (method, path, data) =>
      privateRequest(operatorPage, method, path, {
        headers: scoped(operator, org),
        data,
      });
    expect((await ownerRequest("GET", legacy)).status()).toBe(404);
    expect(
      (
        await ownerRequest("GET", `${attemptPath}/lid-practice-result/status`)
      ).status(),
    ).toBe(404);
    expect(
      (await ownerRequest("POST", `${releaseAction}/approve`)).status(),
    ).toBe(403);
    expect(
      (
        await ownerRequest("POST", `${releaseAction}/release`, {
          operation_id: randomUUID(),
        })
      ).status(),
    ).toBe(403);
    expect(
      (
        await staff("POST", `${releaseAction}/release`, {
          operation_id: randomUUID(),
        })
      ).status(),
    ).toBe(409);
    expect(
      (await staff("POST", `${releaseAction}/request-review`)).status(),
    ).toBe(200);
    expect((await staff("POST", `${releaseAction}/approve`)).status()).toBe(
      200,
    );
    const releaseInput = { operation_id: randomUUID() };
    const released = await staff(
      "POST",
      `${releaseAction}/release`,
      releaseInput,
    );
    expect(released.status()).toBe(200);
    const { release_id: releaseId } = await released.json();
    expect(
      (
        await (
          await staff("POST", `${releaseAction}/release`, releaseInput)
        ).json()
      ).release_id,
    ).toBe(releaseId);
    expect(
      (
        await staff(
          "POST",
          `${institution}/releases/${releaseId}/certificate?authorization_id=${randomUUID()}`,
          { operation_id: randomUUID() },
        )
      ).status(),
    ).toBe(403);
    expect(
      (
        await staff("POST", `${institution}/releases/${releaseId}/statement`, {
          operation_id: randomUUID(),
        })
      ).status(),
    ).toBe(200);
    expect((await ownerRequest("GET", legacy)).status()).toBe(200);

    const outsider = await login(
      outsiderPage,
      "speaking.outsider@demo.prufai.local",
    );
    const outside = outsider.profile.memberships[0].organisation_id;
    expect(outside).not.toBe(org);
    await outsiderPage.goto(`/candidate/results/releases/${releaseId}`);
    await expect(
      outsiderPage.getByText("release not found", { exact: true }),
    ).toBeVisible();
    for (const path of [
      legacy,
      `${candidate}/releases/${releaseId}/statement`,
    ]) {
      expect(
        (
          await privateRequest(outsiderPage, "GET", path, {
            headers: scoped(outsider, outside),
          })
        ).status(),
      ).toBe(404);
    }

    await page.goto(`/candidate/exams/${attempt}`);
    const preview = page.getByRole("region", { name: "LiD practice result" });
    await expect(preview).toContainText("answers correct");
    await login(scorePage, candidateEmail);
    const releasePage = `/candidate/results/releases/${releaseId}`;
    await scorePage.goto(releasePage);
    await expect(
      scorePage.getByRole("heading", { name: "Released score" }),
    ).toBeVisible();
    await expect(
      scorePage.getByText("Statement issued:", { exact: false }),
    ).toBeVisible();
    await scorePage.context().setOffline(true);
    await expect(
      scorePage.getByRole("heading", { name: "Released score" }),
    ).toHaveCount(0);
    await expect(
      scorePage.getByText("Statement issued:", { exact: false }),
    ).toHaveCount(0);

    // Keep the existing authenticated tab open across the deployment stop.
    await restartOwnedApi("BERLIN_LID_LOCKED");
    await page.evaluate(() => window.dispatchEvent(new Event("pageshow")));
    await expect(preview).not.toContainText("answers correct");
    expect((await ownerRequest("GET", legacy)).status()).toBe(404);

    await restartOwnedApi("BERLIN_LID_SYNTHETIC");
    await page.reload();
    await expect(preview).toContainText("answers correct");

    // Hold an actual authorized API response across revocation. The browser
    // must check the current release before it renders the delayed outcome.
    let resume;
    const gate = new Promise((resolve) => {
      resume = resolve;
    });
    let captured = false;
    await page.route(`**${attemptPath}/lid-practice-result`, async (route) => {
      const snapshot = await callUpstream(route);
      captured = snapshot.status() === 200;
      await gate;
      await route.fulfill({ response: snapshot });
    });
    try {
      await page.evaluate(() => window.dispatchEvent(new Event("pageshow")));
      await expect.poll(() => captured).toBe(true);
      await expect(preview).not.toContainText("answers correct");
      const revoke = {
        reason: "Synthetic confidentiality regression",
        operation_id: randomUUID(),
      };
      const action = `${institution}/releases/${releaseId}/revoke`;
      const [revoked, repeated] = [
        await staff("POST", action, revoke),
        await staff("POST", action, revoke),
      ];
      expect(revoked.status()).toBe(200);
      expect(repeated.status()).toBe(200);
      expect(await revoked.json()).toEqual(await repeated.json());
    } finally {
      resume();
    }
    await expect(preview).toContainText("bestätigter Freigabe");
    await expect(preview).not.toContainText("answers correct");
    const stale = await ownerRequest("GET", legacy);
    expect(stale.status()).toBe(404);
    expect(stale.headers()["cache-control"]).toContain("no-store");
    expect(
      (
        await ownerRequest(
          "GET",
          `${candidate}/releases/${releaseId}/statement`,
        )
      ).status(),
    ).toBe(404);
    await scorePage.context().setOffline(false);
    await scorePage.reload();
    await expect(
      scorePage.locator("p").filter({ hasText: /^Effective status: REVOKED$/ }),
    ).toContainText("REVOKED");
    await expect(
      scorePage.getByRole("heading", { name: "Released score" }),
    ).toHaveCount(0);
    await expect(
      scorePage.getByText("Statement issued:", { exact: false }),
    ).toHaveCount(0);
    await page.unroute(`**${attemptPath}/lid-practice-result`);
    await page.reload();
    await expect(preview).toContainText("reviews and releases");
    await expect(preview).not.toContainText("answers correct");
    await logout(page);
    await login(page, "history@demo.prufai.local");
    await page.goto(`/candidate/exams/${attempt}`);
    await expect(
      page.getByRole("region", { name: "LiD practice result" }),
    ).toHaveCount(0);
    await expect(page.getByText("answers correct")).toHaveCount(0);
  } finally {
    await Promise.all([
      operatorPage.close(),
      outsiderPage.close(),
      scorePage.close(),
    ]);
    await restartOwnedApi();
  }
});
