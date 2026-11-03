const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const path = require("node:path");

const password = process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!";
const accounts = {
  platform: "platform.admin@demo.prufai.local",
  institution: "admin@demo.prufai.local",
  candidate: "golden.candidate@demo.prufai.local",
};

async function login(page, email) {
  await page.goto("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith("/api/v1/auth/login") &&
      item.request().method() === "POST",
  );
  const identity = page.waitForResponse(
    (item) => item.url().endsWith("/api/v1/auth/me") && item.status() === 200,
  );
  await page
    .getByRole("button", { name: "Sign in to workspace", exact: true })
    .click();
  expect((await response).status()).toBe(200);
  const token = (await (await response).json()).access_token;
  const profile = await (await identity).json();
  await expect(page).not.toHaveURL(/\/login$/);
  return { token, profile };
}

async function logout(page) {
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith("/api/v1/auth/logout") &&
      item.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  expect((await response).status()).toBe(204);
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
}

function apiResponse(page, suffix, method = "GET") {
  return page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname.endsWith(suffix) &&
      item.request().method() === method,
  );
}

async function screenshot(page, name) {
  await page.screenshot({
    path: path.join(process.env.PRUFAI_E2E_ARTIFACT_DIR, name),
    fullPage: true,
  });
}

test("three applications complete a real objective journey and reject its exact-result appeal", async ({
  page,
  context,
}) => {
  let institution;
  let candidate;
  let examVersionId;
  let packVersionId;
  let assignmentId;
  let attemptId;
  let result;
  let releaseId;

  await test.step("platform inspects real pack and published exam provenance", async () => {
    await login(page, accounts.platform);
    await expect(page).toHaveURL(/\/control-center$/);
    await page
      .getByRole("link", { name: "Standards & packs", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: /Standard/ }).first(),
    ).toBeVisible();
    await expect(page.getByText("B1", { exact: true }).first()).toBeVisible();
    await page
      .getByRole("link", { name: "Organisations", exact: true })
      .click();
    await page
      .getByRole("link", { name: "Exam metadata", exact: true })
      .click();
    await expect(
      page.getByText("PRACTICE", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("ACTIVE", { exact: true }).first(),
    ).toBeVisible();
    await screenshot(page, "platform-exam-provenance.png");
    await logout(page);
  });

  await test.step("institution assigns an exact published exam through the product", async () => {
    institution = await login(page, accounts.institution);
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Assign a practice assessment" }),
    ).toBeVisible();
    const candidateOption = page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .locator("option")
      .filter({ hasText: /Golden/ });
    const examOption = page
      .getByRole("combobox", { name: "Practice exam version", exact: true })
      .locator("option")
      .filter({ hasText: /Golden|Integrated/ });
    await expect(candidateOption).toHaveCount(1);
    await expect(examOption).toHaveCount(1);
    await page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .selectOption(await candidateOption.getAttribute("value"));
    examVersionId = await examOption.getAttribute("value");
    await page
      .getByRole("combobox", { name: "Practice exam version", exact: true })
      .selectOption(examVersionId);
    const response = apiResponse(page, "/exam-assignments", "POST");
    await page
      .getByRole("button", { name: "Assign assessment", exact: true })
      .click();
    const created = await response;
    expect(created.status()).toBe(201);
    assignmentId = (await created.json()).id;
    await expect(
      page.getByText(
        "Assessment assigned. The candidate can now open it in My exams.",
        { exact: true },
      ),
    ).toBeVisible();
    await logout(page);
  });

  await test.step("candidate starts, saves twice, reloads, resumes and submits", async () => {
    candidate = await login(page, accounts.candidate);
    await expect(
      page.getByRole("heading", { name: "My exams", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", {
        name: "Prepare exam for exam assignment",
        exact: true,
      }),
    ).toHaveCount(1);
    await page
      .getByRole("link", {
        name: "Prepare exam for exam assignment",
        exact: true,
      })
      .click();
    const response = apiResponse(page, "/attempts", "POST");
    await page
      .getByRole("button", { name: "Prepare exam", exact: true })
      .click();
    const attempt = await response;
    expect(attempt.ok()).toBe(true);
    attemptId = (await attempt.json()).id;
    await page.getByRole("button", { name: "Start exam", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Runtime", exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/SYNTHETIC DEMO DATA/)).toBeVisible();
    // The fixture labels intentionally remain visible and original; no answer
    // key or successful result is fetched/injected by this acceptance test.
    const options = page.getByRole("radio");
    await expect(options).toHaveCount(2);
    await page.getByRole("radio", { name: "Um 10 Uhr.", exact: true }).check();
    await expect(
      page.getByText("Saved response revision: 1", { exact: true }),
    ).toBeVisible();
    await page.getByRole("radio", { name: "Um 9 Uhr.", exact: true }).check();
    await expect(
      page.getByText("Saved response revision: 2", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByText("Saved response revision: 2", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("radio").nth(0)).toBeChecked();
    const submitted = apiResponse(page, "/submit", "POST");
    await page
      .getByRole("button", { name: "Submit attempt", exact: true })
      .click();
    expect((await submitted).ok()).toBe(true);
    await expect(
      page.getByText("Autosave: Submitted", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("radio").nth(0)).toBeDisabled();
    await page.goto("/candidate/results");
    await expect(
      page.getByText("No released result is available.", { exact: true }),
    ).toBeVisible();
    await logout(page);
  });

  await test.step("actual scoring persists a result and a human approves its exact checksum", async () => {
    institution = await login(page, accounts.institution);
    const assignment = page
      .getByRole("article")
      .filter({ hasText: "Golden B1 Practice Candidate" });
    await expect(assignment).toHaveCount(1);
    const scored = apiResponse(
      page,
      `/exam-attempts/${attemptId}/score`,
      "POST",
    );
    await assignment
      .getByRole("button", { name: "Score submitted attempt", exact: true })
      .click();
    expect((await scored).status()).toBe(200);
    await assignment
      .getByRole("link", {
        name: "Inspect score, review and release →",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", {
        name: "Result review and release",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.locator("p").filter({ hasText: /^Raw score:/ }),
    ).toHaveText(/Raw score:\s*1(?:\.0+)?\s*\/\s*1(?:\.0+)?/);
    const versions = await page.request.get(
      `/api/v1/organisations/${institution.profile.memberships[0].organisation_id}/results/${page.url().split("/").pop()}/versions`,
      {
        headers: {
          Authorization: `Bearer ${institution.token}`,
          "X-PrufAI-Organisation-ID":
            institution.profile.memberships[0].organisation_id,
        },
      },
    );
    expect(versions.status()).toBe(200);
    [result] = await versions.json();
    expect(result.attempt_id).toBe(attemptId);
    expect(result.scoring_run_id).toBeTruthy();
    const organisationId = institution.profile.memberships[0].organisation_id;
    const unapproved = await page.request.post(
      `/api/v1/organisations/${organisationId}/result-versions/${result.result_version_id}/release`,
      {
        headers: {
          Authorization: `Bearer ${institution.token}`,
          "X-PrufAI-Organisation-ID": organisationId,
        },
        data: { operation_id: randomUUID() },
      },
    );
    expect(unapproved.status()).toBe(409);
    await expect(
      page.getByRole("button", {
        name: "Release approved result",
        exact: true,
      }),
    ).toHaveCount(0);
    const requested = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/request-review`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Request human result review", exact: true })
      .click();
    expect((await requested).status()).toBe(200);
    await expect(
      page.getByRole("button", {
        name: "Approve reviewed result",
        exact: true,
      }),
    ).toBeVisible();
    await page.reload();
    await page
      .getByRole("button", { name: "Approve reviewed result", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Release approved result",
        exact: true,
      }),
    ).toBeVisible();
    const released = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/release`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Release approved result", exact: true })
      .click();
    const recorded = await released;
    expect(recorded.status()).toBe(200);
    releaseId = (await recorded.json()).release_id;
    await expect(page).toHaveURL(
      new RegExp(`/institution/releases/${releaseId}$`),
    );
    await expect(
      page.getByText(result.scoring_run_id, { exact: true }),
    ).toBeVisible();
    const issued = apiResponse(
      page,
      `/releases/${releaseId}/statement`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Issue Statement of Results", exact: true })
      .click();
    const statement = await (await issued).json();
    expect(statement.payload.result_version_id).toBe(result.result_version_id);
    expect(statement.payload.scoring_run_id).toBe(result.scoring_run_id);
    expect(statement.payload.attempt_id).toBe(attemptId);
    expect(statement.payload.candidate_user_id).toBe(candidate.profile.id);
    expect(statement.payload.issued_by_user_id).toBe(institution.profile.id);
    expect(statement.payload.classification).toBe("PRACTICE");
    packVersionId = statement.payload.standard_pack_version_id;
    await logout(page);
  });

  await test.step("candidate sees the released score and structured Statement of Results", async () => {
    await login(page, accounts.candidate);
    await page
      .getByRole("link", { name: "View released results", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: "View result and Statement of Results",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", { name: "Statement of Results", exact: true }),
    ).toBeVisible();
    await expect(page.getByText(packVersionId, { exact: true })).toBeVisible();
    await expect(
      page.getByText(result.scoring_run_id, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/not an official provider certificate/i),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await screenshot(page, "candidate-result-mobile.png");
    await page.setViewportSize({ width: 1280, height: 900 });
    await screenshot(page, "candidate-result-and-statement.png");
  });

  await test.step("appeal exact R1, human rejects it, original result remains", async () => {
    await page
      .getByRole("link", { name: "Appeal this result version", exact: true })
      .click();
    await expect(
      page.getByRole("combobox", {
        name: "Released result version",
        exact: true,
      }),
    ).toHaveValue(result.result_version_id);
    await page.getByLabel("Category", { exact: true }).fill("SCORING_QUERY");
    await page
      .getByLabel("Grounds", { exact: true })
      .fill(
        "Synthetic walkthrough: please verify the pinned objective answer and deterministic score.",
      );
    const submitted = apiResponse(
      page,
      `/results/${result.result_version_id}/appeals`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Submit appeal", exact: true })
      .click();
    const submittedResponse = await submitted;
    expect(submittedResponse.ok()).toBe(true);
    const appealId = (await submittedResponse.json()).id;
    await logout(page);
    await login(page, accounts.institution);
    await page.goto("/institution/appeals");
    await page.getByRole("link", { name: appealId, exact: true }).click();
    await expect(
      page.getByRole("heading", {
        name: "Original objective item 1",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.locator("p").filter({ hasText: /^Submitted selection:/ }),
    ).toContainText("a: Um 9 Uhr.");
    await expect(
      page.locator("p").filter({ hasText: /^Expected selection:/ }),
    ).toContainText("a: Um 9 Uhr.");
    await expect(
      page.locator("p").filter({ hasText: /^Recorded item score:/ }),
    ).toHaveText(/Recorded item score:\s*1(?:\.0+)?\s*\/\s*1(?:\.0+)?/);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await screenshot(page, "institution-pinned-evidence-mobile.png");
    await page.setViewportSize({ width: 1280, height: 900 });
    await page
      .getByRole("button", { name: "Start review", exact: true })
      .click();
    await page
      .getByLabel("Decision reason", { exact: true })
      .fill(
        "Reviewed the original pinned response and exact deterministic evidence. The recorded objective score is correct; this synthetic appeal is rejected.",
      );
    const adjudicated = apiResponse(
      page,
      `/appeals/${appealId}/adjudicate`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Reject reviewed appeal", exact: true })
      .click();
    expect((await adjudicated).ok()).toBe(true);
    await expect(
      page.getByText("ADJUDICATED_REJECTED", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("No correction initiated", { exact: true }),
    ).toBeVisible();
    await logout(page);
    await login(page, accounts.candidate);
    await page.goto(`/candidate/results/appeals/${appealId}`);
    await expect(
      page.getByText("Status: ADJUDICATED_REJECTED", { exact: true }),
    ).toBeVisible();
    await page.goto(`/candidate/results/releases/${releaseId}`);
    await expect(
      page.getByText(result.result_version_id, { exact: true }).first(),
    ).toBeVisible();
    await logout(page);
  });

  await test.step("platform inspects safe release audit without candidate answers", async () => {
    await login(page, accounts.platform);
    await page
      .getByRole("link", { name: "Audit evidence", exact: true })
      .click();
    await page
      .getByLabel("Exact event type", { exact: true })
      .fill("result.released");
    await page
      .getByRole("button", { name: "Apply filters", exact: true })
      .click();
    await expect(
      page.getByRole("cell", { name: "result.released", exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("cell", { name: result.result_version_id, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/option_id|candidate speech|storage_key|password_hash/),
    ).toHaveCount(0);
    await screenshot(page, "platform-safe-audit.png");
    await logout(page);
  });
  expect(assignmentId).toBeTruthy();
  expect(examVersionId).toBeTruthy();
});

test("cross-role UI and backend authority deny protected operations", async ({
  page,
}) => {
  const institution = await login(page, accounts.institution);
  await page.goto("/control-center");
  await expect(
    page.getByRole("heading", { name: "Access denied", exact: true }),
  ).toBeVisible();
  const forbidden = await page.request.get(
    "/api/v1/platform/control-center/overview",
    { headers: { Authorization: `Bearer ${institution.token}` } },
  );
  expect(forbidden.status()).toBe(403);
  await page
    .getByRole("link", { name: "Return to your workspace", exact: true })
    .click();
  await logout(page);
  const candidate = await login(page, accounts.candidate);
  await page.goto("/institution/releases");
  await expect(
    page.getByRole("heading", { name: "Access restricted", exact: true }),
  ).toBeVisible();
  const org = candidate.profile.memberships[0].organisation_id;
  const denied = await page.request.post(
    `/api/v1/organisations/${org}/result-versions/${randomUUID()}/release`,
    {
      headers: {
        Authorization: `Bearer ${candidate.token}`,
        "X-PrufAI-Organisation-ID": org,
      },
      data: { operation_id: randomUUID() },
    },
  );
  expect(denied.status()).toBe(403);
  await logout(page);
  const unauthenticated = await page.request.get("/api/v1/candidate/releases");
  expect(unauthenticated.status()).toBe(401);
});

test("logout clears real account-partitioned offline responses before account switch", async ({
  page,
  context,
}) => {
  await login(page, "candidate@demo.prufai.local");
  await page
    .getByRole("link", {
      name: "Prepare exam for exam assignment",
      exact: true,
    })
    .click();
  await page.getByRole("button", { name: "Prepare exam", exact: true }).click();
  await page.getByRole("button", { name: "Start exam", exact: true }).click();
  await expect(page.getByRole("radio").first()).toBeEnabled();
  await context.setOffline(true);
  await page.getByRole("radio").first().check();
  await expect(
    page.getByText("Autosave: Offline — queued", { exact: true }),
  ).toBeVisible();
  const before = await page.evaluate(async () =>
    (await indexedDB.databases()).filter((database) =>
      database.name?.startsWith("prufai-candidate-runtime-v2:"),
    ),
  );
  expect(before).toHaveLength(1);
  // Abort only sync requests during reconnection so the real queued response
  // remains pending until logout purges it. No response is mocked as success.
  await page.route("**/api/v1/candidate/exam-attempts/*/responses/*", (route) =>
    route.abort("internetdisconnected"),
  );
  await context.setOffline(false);
  await logout(page);
  await page.unroute("**/api/v1/candidate/exam-attempts/*/responses/*");
  const remaining = await page.evaluate(async () =>
    (await indexedDB.databases()).filter((database) =>
      database.name?.startsWith("prufai-candidate-runtime-v2:"),
    ),
  );
  expect(remaining).toHaveLength(0);
  await login(page, accounts.institution);
  await expect(
    page.getByRole("heading", { name: "Institution inspection", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("radio")).toHaveCount(0);
  await logout(page);
  await login(page, "candidate@demo.prufai.local");
  await page
    .getByRole("link", { name: "Resume for exam assignment", exact: true })
    .click();
  await expect(
    page.getByText("Saved response revision: 0", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("radio").first()).not.toBeChecked();
  await logout(page);
});
