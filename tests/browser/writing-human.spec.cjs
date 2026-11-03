const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

const ADMIN = "admin@demo.prufai.local";
const REVIEWER = "reviewer@demo.prufai.local";
const CANDIDATE = "writing.candidate@demo.prufai.local";
const INITIAL_TEXT =
  "Liebe Frau Sommer, am Dienstag bin ich beim Arzt. Bitte schicken Sie mir die Hausaufgaben. Am Donnerstag bin ich wieder da. Viele Grüße, Mira Beispiel";
const SUBMITTED_TEXT =
  "Liebe Frau Sommer,\nam Dienstag kann ich leider nicht zum Deutschkurs kommen, weil ich einen wichtigen Termin beim Arzt habe. Können Sie mir bitte die Hausaufgaben per E-Mail schicken? Ich möchte zu Hause weiterlernen und die Übungen vor dem nächsten Unterricht machen. Am Donnerstag bin ich wieder im Kurs. Vielen Dank für Ihre Hilfe und Ihr Verständnis.\nMit freundlichen Grüßen\nMira Beispiel";
const QUOTE = "Am Donnerstag bin ich wieder im Kurs.";

test("humans publish a rubric and independently approve exact Writing evidence without AI", async ({
  page,
  context,
}) => {
  const aiRequests = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (
      request.method() === "POST" &&
      (/\/ai\/.+\/run$/.test(pathname) ||
        /\/subjective-assessments\/[^/]+\/propose$/.test(pathname))
    )
      aiRequests.push(pathname);
  });
  let rubric;
  let rubricPath;
  let attemptId;
  let candidate;
  let assessmentId;
  let versionId;
  let createdChecksum;

  await test.step("authorized requester submits the draft rubric for independent review", async () => {
    const actor = await login(page, ADMIN);
    await page
      .getByRole("link", { name: "Writing rubrics", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: "Inspect rubric human-writing-b1-v1",
        exact: true,
      })
      .click();
    await expect(
      page.getByText("Status: DRAFT", { exact: true }),
    ).toBeVisible();
    rubricPath = new URL(page.url()).pathname;
    const requested = apiResponse(page, "/request-review", "POST");
    await page
      .getByRole("button", { name: "Request rubric review", exact: true })
      .click();
    const response = await requested;
    expect(response.status()).toBe(200);
    rubric = await response.json();
    await expect(
      page.getByText("Status: HUMAN_REVIEW", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", {
        name: "Approve reviewed rubric",
        exact: true,
      }),
    ).toHaveCount(0);
    const org = actor.profile.memberships[0].organisation_id;
    const selfApproval = await page.request.post(
      `/api/v1/organisations/${org}${rubricPath.replace("/institution", "")}/approve`,
      {
        headers: {
          Authorization: `Bearer ${actor.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
        data: {
          operation_id: randomUUID(),
          expected_checksum: rubric.checksum,
        },
      },
    );
    expect([403, 409]).toContain(selfApproval.status());
    await logout(page);
  });

  await test.step("a different authorized human approves and publishes the pinned rubric", async () => {
    await login(page, REVIEWER);
    await page.goto(rubricPath);
    await page
      .getByRole("button", { name: "Approve reviewed rubric", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Publish approved rubric", exact: true })
      .waitFor({ state: "visible" });
    const published = apiResponse(page, "/publish", "POST");
    await page
      .getByRole("button", { name: "Publish approved rubric", exact: true })
      .click();
    expect((await published).status()).toBe(200);
    await expect(
      page.getByText("Status: PUBLISHED", { exact: true }),
    ).toBeVisible();
    await screenshot(page, "writing-rubric-published.png");
    await logout(page);
  });

  await test.step("institution assigns the original Writing exam through the existing product", async () => {
    await login(page, ADMIN);
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    const candidateSelect = page.getByRole("combobox", {
      name: "Candidate",
      exact: true,
    });
    await candidateSelect.selectOption({
      label: "Human Writing Practice Candidate",
    });
    const examSelect = page.getByRole("combobox", {
      name: "Practice exam version",
      exact: true,
    });
    const option = examSelect
      .locator("option")
      .filter({ hasText: "Human B1 German Writing Practice" });
    await expect(option).toHaveCount(1);
    await examSelect.selectOption(await option.getAttribute("value"));
    const assigned = apiResponse(page, "/exam-assignments", "POST");
    await page
      .getByRole("button", { name: "Assign assessment", exact: true })
      .click();
    expect((await assigned).status()).toBe(201);
    await logout(page);
  });

  await test.step("candidate saves two real Writing revisions and resumes after reload", async () => {
    candidate = await login(page, CANDIDATE);
    await page
      .getByRole("link", {
        name: "Prepare exam for exam assignment",
        exact: true,
      })
      .click();
    const prepared = apiResponse(page, "/attempts", "POST");
    await page
      .getByRole("button", { name: "Prepare exam", exact: true })
      .click();
    attemptId = (await (await prepared).json()).id;
    await page.getByRole("button", { name: "Start exam", exact: true }).click();
    const writing = page.getByRole("textbox", {
      name: "Long response",
      exact: true,
    });
    await writing.fill(INITIAL_TEXT);
    await page.getByRole("heading", { name: "Question", exact: true }).click();
    await expect(
      page.getByText("Saved response revision: 1", { exact: true }),
    ).toBeVisible();
    await writing.fill(SUBMITTED_TEXT);
    await page.getByRole("heading", { name: "Question", exact: true }).click();
    await expect(
      page.getByText("Saved response revision: 2", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(writing).toHaveValue(SUBMITTED_TEXT);
    await expect(
      page.getByText("Saved response revision: 2", { exact: true }),
    ).toBeVisible();
  });

  await test.step("logout purges an offline Writing draft before account switch", async () => {
    const writing = page.getByRole("textbox", {
      name: "Long response",
      exact: true,
    });
    await context.setOffline(true);
    await writing.fill(`${SUBMITTED_TEXT}\nPRIVATE SYNTHETIC OFFLINE DRAFT`);
    await page.getByRole("heading", { name: "Question", exact: true }).click();
    await expect(
      page.getByText("Autosave: Offline — queued", { exact: true }),
    ).toBeVisible();
    await page.route(
      "**/api/v1/candidate/exam-attempts/*/responses/*",
      (route) => route.abort("internetdisconnected"),
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
    await login(page, REVIEWER);
    await expect(page.getByText(/PRIVATE SYNTHETIC OFFLINE DRAFT/)).toHaveCount(
      0,
    );
    await logout(page);
    await login(page, CANDIDATE);
    await page
      .getByRole("link", { name: "Resume for exam assignment", exact: true })
      .click();
    await expect(
      page.getByRole("textbox", { name: "Long response", exact: true }),
    ).toHaveValue(SUBMITTED_TEXT);
    await expect(
      page.getByText("Saved response revision: 2", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Submit attempt", exact: true })
      .click();
    await expect(
      page.getByText("Autosave: Submitted", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "Long response", exact: true }),
    ).toBeDisabled();
    await logout(page);
  });

  await test.step("assessor records bounded human judgments against the published rubric", async () => {
    const actor = await login(page, REVIEWER);
    await page
      .getByRole("link", { name: "Writing review", exact: true })
      .click();
    const sourceSelect = page.getByRole("combobox", {
      name: "Submitted Writing response",
      exact: true,
    });
    const source = sourceSelect
      .locator("option")
      .filter({ hasText: "response" })
      .filter({ hasNotText: "Select an exact" });
    await expect(source).toHaveCount(1);
    await sourceSelect.selectOption(await source.getAttribute("value"));
    const rubricSelect = page.getByRole("combobox", {
      name: "Published Writing rubric",
      exact: true,
    });
    const published = rubricSelect
      .locator("option")
      .filter({ hasText: "human-writing-b1-v1" });
    await rubricSelect.selectOption(await published.getAttribute("value"));
    const initialized = apiResponse(
      page,
      "/subjective-assessments/human",
      "POST",
    );
    await page
      .getByRole("button", {
        name: "Initialize human Writing assessment",
        exact: true,
      })
      .click();
    const initializedResponse = await initialized;
    expect(initializedResponse.ok()).toBe(true);
    const created = await initializedResponse.json();
    assessmentId = created.id;
    versionId = created.version_id;
    createdChecksum = created.checksum;
    await expect(page.getByText(SUBMITTED_TEXT, { exact: true })).toBeVisible();
    await expect(
      page.locator("p").filter({ hasText: /^Assessment origin:/ }),
    ).toContainText("HUMAN");
    for (const [code, points, level] of [
      ["TASK", "3", "CLEAR"],
      ["LANGUAGE", "2", "DEVELOPING"],
    ]) {
      await page
        .getByRole("spinbutton", { name: `Points for ${code}`, exact: true })
        .fill(points);
      await page
        .getByRole("combobox", {
          name: `Performance level for ${code}`,
          exact: true,
        })
        .selectOption(level);
      await page
        .getByRole("textbox", {
          name: `Exact response quote for ${code} (required)`,
          exact: true,
        })
        .fill(QUOTE);
      await page
        .getByRole("textbox", {
          name: `Human rationale for ${code}`,
          exact: true,
        })
        .fill(
          `Synthetic human review of ${code}: the quoted original response supports this practice judgment under the exact descriptor.`,
        );
    }
    const proposed = apiResponse(page, "/human-proposal", "POST");
    await page
      .getByRole("button", {
        name: "Submit human assessment for independent review",
        exact: true,
      })
      .click();
    const response = await proposed;
    expect(response.status()).toBe(200);
    const proposal = await response.json();
    expect(proposal.status).toBe("IN_REVIEW");
    const org = actor.profile.memberships[0].organisation_id;
    const replay = await page.request.post(
      `/api/v1/organisations/${org}/subjective-assessments/${versionId}/human-proposal`,
      {
        headers: {
          Authorization: `Bearer ${actor.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
        data: response.request().postDataJSON(),
      },
    );
    expect(replay.status()).toBe(200);
    await expect(
      page.getByRole("button", {
        name: "Approve human Writing assessment",
        exact: true,
      }),
    ).toHaveCount(0);
    await screenshot(page, "writing-human-awaiting-independent-review.png");
    await logout(page);
  });

  await test.step("an independent authorized human approves the exact assessment version", async () => {
    const actor = await login(page, ADMIN);
    await page.goto(`/institution/writing-reviews/${assessmentId}`);
    await page
      .getByRole("button", {
        name: "Approve human Writing assessment",
        exact: true,
      })
      .waitFor({ state: "visible" });
    const org = actor.profile.memberships[0].organisation_id;
    const stale = await page.request.post(
      `/api/v1/organisations/${org}/subjective-assessments/${versionId}/human-approve`,
      {
        headers: {
          Authorization: `Bearer ${actor.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
        data: {
          operation_id: randomUUID(),
          expected_checksum: createdChecksum,
        },
      },
    );
    expect(stale.status()).toBe(409);
    const candidateDenied = await page.request.post(
      `/api/v1/organisations/${org}/subjective-assessments/${versionId}/human-approve`,
      {
        headers: {
          Authorization: `Bearer ${candidate.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
        data: {
          operation_id: randomUUID(),
          expected_checksum: createdChecksum,
        },
      },
    );
    expect([401, 403]).toContain(candidateDenied.status());
    const approved = apiResponse(page, "/human-approve", "POST");
    await page
      .getByRole("button", {
        name: "Approve human Writing assessment",
        exact: true,
      })
      .click();
    expect((await approved).status()).toBe(200);
    await expect(
      page.locator("p").filter({ hasText: /^Assessment status:/ }),
    ).toContainText("APPROVED");
    await page.reload();
    await expect(
      page.locator("p").filter({ hasText: /^Assessment status:/ }),
    ).toContainText("APPROVED");
    await expect(
      page.getByRole("button", {
        name: "Submit human assessment for independent review",
        exact: true,
      }),
    ).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await page.setViewportSize({ width: 1280, height: 900 });
    await screenshot(page, "writing-human-approved.png");
    expect(aiRequests).toEqual([]);
    await logout(page);
  });

  await test.step("platform audit exposes safe governance references", async () => {
    await login(page, "platform.admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Audit evidence", exact: true })
      .click();
    await page
      .getByRole("textbox", { name: "Exact event type", exact: true })
      .fill("subjective_assessment.human_approved");
    const filteredAudit = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname.endsWith("/platform/control-center/audit") &&
        url.searchParams.get("event_type") ===
          "subjective_assessment.human_approved" &&
        response.request().method() === "GET"
      );
    });
    await page
      .getByRole("button", { name: "Apply filters", exact: true })
      .click();
    expect((await filteredAudit).ok()).toBe(true);
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: "subjective_assessment.human_approved" })
        .getByRole("cell", { name: versionId, exact: true }),
    ).toBeVisible();
    await expect(page.getByText(SUBMITTED_TEXT, { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByText(
        /PRIVATE SYNTHETIC OFFLINE DRAFT|quoted original response supports/,
      ),
    ).toHaveCount(0);
    await logout(page);
  });
  expect(attemptId).toBeTruthy();
});
