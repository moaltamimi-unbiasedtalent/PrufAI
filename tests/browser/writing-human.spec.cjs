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

test("three applications complete an independently approved human Writing result and statement without AI", async ({
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
  let institution;
  let pendingRun;
  let pendingResult;
  let result;
  let releaseId;
  let statement;

  async function versionsFor(resultId) {
    const org = institution.profile.memberships[0].organisation_id;
    const response = await page.request.get(
      `/api/v1/organisations/${org}/results/${resultId}/versions`,
      {
        headers: {
          Authorization: `Bearer ${institution.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
      },
    );
    expect(response.status()).toBe(200);
    return (await response.json()).sort(
      (a, b) => b.version_number - a.version_number,
    );
  }

  function writingAssignment() {
    return page
      .getByRole("article")
      .filter({ hasText: "Human Writing Practice Candidate" });
  }

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
      .focus();
    await page.keyboard.press("Enter");
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
    const publishedResponse = await published;
    expect(publishedResponse.status()).toBe(200);
    rubric = await publishedResponse.json();
    expect(rubric.language).toBe("de");
    expect(rubric.cefr_levels).toEqual(["B1"]);
    await expect(
      page.getByText("Status: PUBLISHED", { exact: true }),
    ).toBeVisible();
    await screenshot(page, "writing-rubric-published.png");
    await logout(page);
  });

  await test.step("platform inspects the exact published Writing Standard Pack", async () => {
    await login(page, "platform.admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Standards & packs", exact: true })
      .click();
    const row = page.getByRole("row").filter({
      has: page.getByRole("cell", {
        name: rubric.standard_pack_version_id,
        exact: true,
      }),
    });
    await expect(row).toBeVisible();
    await expect(
      row.getByRole("cell", { name: "de", exact: true }),
    ).toBeVisible();
    await expect(
      row.getByRole("cell", { name: "B1", exact: true }),
    ).toBeVisible();
    await expect(
      row.getByRole("cell", { name: "PRACTICE", exact: true }),
    ).toBeVisible();
    await screenshot(page, "writing-platform-pack-provenance.png");
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
    await writing.press("Tab");
    await expect(
      page.getByText("Saved response revision: 1", { exact: true }),
    ).toBeVisible();
    await writing.fill(SUBMITTED_TEXT);
    await writing.press("Tab");
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
    institution = actor;
    const org = actor.profile.memberships[0].organisation_id;
    await page.goto("/institution/overview");
    const pending = apiResponse(
      page,
      `/exam-attempts/${attemptId}/score`,
      "POST",
    );
    await writingAssignment()
      .getByRole("button", { name: "Score submitted attempt", exact: true })
      .click();
    const pendingResponse = await pending;
    expect(pendingResponse.status()).toBe(200);
    pendingRun = await pendingResponse.json();
    await writingAssignment()
      .getByRole("link", {
        name: "Inspect score, review and release →",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/\/institution\/results\/[0-9a-f-]+$/);
    [pendingResult] = await versionsFor(
      new URL(page.url()).pathname.split("/").pop(),
    );
    expect(pendingResult.status).toBe("INCOMPLETE");
    expect(pendingResult.incomplete_reason).toBe("PENDING_ASSESSMENT");
    expect(pendingResult.scoring_run_id).toBe(pendingRun.id);
    for (const action of ["request-review", "release"]) {
      const denied = await page.request.post(
        `/api/v1/organisations/${org}/result-versions/${pendingResult.result_version_id}/${action}`,
        {
          headers: {
            Authorization: `Bearer ${actor.token}`,
            "X-PrufAI-Organisation-ID": org,
          },
          data:
            action === "release" ? { operation_id: randomUUID() } : undefined,
        },
      );
      expect(denied.status()).toBe(409);
    }
    await expect(
      page.getByRole("button", {
        name: "Release approved result",
        exact: true,
      }),
    ).toHaveCount(0);
    await page.goto(`/institution/writing-reviews/${assessmentId}`);
    await page
      .getByRole("button", {
        name: "Approve human Writing assessment",
        exact: true,
      })
      .waitFor({ state: "visible" });
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
    const feedbackPanel = page.getByRole("region", {
      name: "Writing assessment feedback",
    });
    await expect(feedbackPanel).toContainText(
      "No feedback is recorded for this assessment version.",
    );
    await feedbackPanel
      .getByRole("combobox", { name: "Feedback category", exact: true })
      .selectOption("RUBRIC_ALIGNMENT");
    await feedbackPanel
      .getByRole("combobox", { name: "Feedback severity", exact: true })
      .selectOption("LOW");
    const flag = apiResponse(page, `/organisations/${org}/feedback`, "POST");
    await feedbackPanel
      .getByRole("button", { name: "Record Writing feedback", exact: true })
      .click();
    const flagResponse = await flag;
    expect(flagResponse.status()).toBe(201);
    const feedback = await flagResponse.json();
    expect(feedback).toMatchObject({
      source_kind: "REVIEWER_FLAG",
      source_id: versionId,
      organisation_id: org,
      category: "RUBRIC_ALIGNMENT",
      evidence_class: "UNLABELLED_OBSERVATION",
      status: "RECEIVED",
    });
    expect(flagResponse.request().postDataJSON().source.artifact).toEqual({
      id: feedback.source_id,
      checksum: feedback.source_checksum,
    });
    expect(JSON.stringify(feedback)).not.toContain(SUBMITTED_TEXT);
    await expect(feedbackPanel.getByRole("list")).toContainText(feedback.id);
    await page.reload();
    await expect(feedbackPanel.getByRole("list")).toContainText(feedback.id);
    await expect(
      page.locator("p").filter({ hasText: /^Assessment status:/ }),
    ).toContainText("APPROVED");
    expect(aiRequests).toEqual([]);
  });

  await test.step("approved human Writing creates a new exact score and result through the product", async () => {
    await page
      .getByRole("link", {
        name: "Inspect submitted attempt and scoring →",
        exact: true,
      })
      .click();
    const scored = apiResponse(
      page,
      `/exam-attempts/${attemptId}/score`,
      "POST",
    );
    await writingAssignment()
      .getByRole("button", { name: "Rescore incomplete attempt", exact: true })
      .click();
    const response = await scored;
    expect(response.status()).toBe(200);
    const run = await response.json();
    expect(run.id).not.toBe(pendingRun.id);
    await writingAssignment()
      .getByRole("link", {
        name: "Inspect score, review and release →",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/\/institution\/results\/[0-9a-f-]+$/);
    const versions = await versionsFor(
      new URL(page.url()).pathname.split("/").pop(),
    );
    [result] = versions;
    expect(result.scoring_run_id).toBe(run.id);
    expect(result.status).toBe("REVIEW_REQUIRED");
    expect(result.outcome).toBe("UNDETERMINED");
    expect(Number(result.raw_awarded_points)).toBe(5);
    expect(Number(result.raw_maximum_points)).toBe(6);
    expect(
      versions.find(
        (version) =>
          version.result_version_id === pendingResult.result_version_id,
      )?.checksum,
    ).toBe(pendingResult.checksum);
    await expect(
      page.locator("p").filter({ hasText: /^Raw score:/ }),
    ).toHaveText(/Raw score:\s*5(?:\.0+)?\s*\/\s*6(?:\.0+)?/);
    await page
      .getByRole("region", { name: "Assessment provenance", exact: true })
      .getByText("Exact approved assessment provenance", { exact: true })
      .first()
      .click();
    await expect(
      page.getByText(versionId, { exact: true }).first(),
    ).toBeVisible();
    const org = institution.profile.memberships[0].organisation_id;
    const replay = await page.request.post(
      `/api/v1/organisations/${org}/exam-attempts/${attemptId}/score`,
      {
        headers: {
          Authorization: `Bearer ${institution.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
      },
    );
    expect(replay.status()).toBe(200);
    expect((await replay.json()).id).toBe(run.id);
  });

  await test.step("result review and release remain separate authorized actions", async () => {
    const org = institution.profile.memberships[0].organisation_id;
    const unapproved = await page.request.post(
      `/api/v1/organisations/${org}/result-versions/${result.result_version_id}/release`,
      {
        headers: {
          Authorization: `Bearer ${institution.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
        data: { operation_id: randomUUID() },
      },
    );
    expect(unapproved.status()).toBe(409);
    const requested = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/request-review`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Request human result review", exact: true })
      .click();
    expect((await requested).status()).toBe(200);
    await page.reload();
    const approved = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/approve`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Approve reviewed result", exact: true })
      .click();
    expect((await approved).status()).toBe(200);
    const released = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/release`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Release approved result", exact: true })
      .click();
    const response = await released;
    expect(response.status()).toBe(200);
    releaseId = (await response.json()).release_id;
    await expect(page).toHaveURL(
      new RegExp(`/institution/releases/${releaseId}$`),
    );
    const issued = apiResponse(
      page,
      `/releases/${releaseId}/statement`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Issue Statement of Results", exact: true })
      .click();
    const artifact = await issued;
    expect(artifact.status()).toBe(200);
    statement = await artifact.json();
    expect(statement.payload.classification).toBe("PRACTICE");
    expect(statement.payload.result_version_id).toBe(result.result_version_id);
    expect(statement.payload.scoring_run_id).toBe(result.scoring_run_id);
    expect(statement.payload.standard_pack_version_id).toBe(
      rubric.standard_pack_version_id,
    );
    expect(statement.payload.attempt_id).toBe(attemptId);
    expect(statement.payload.candidate_user_id).toBe(candidate.profile.id);
    expect(statement.payload.issued_by_user_id).toBe(institution.profile.id);
    expect(Number.isNaN(Date.parse(statement.issued_at))).toBe(false);
    expect(Date.parse(statement.payload.issued_at)).toBe(
      Date.parse(statement.issued_at),
    );
    expect(statement.payload.assessment_components).toHaveLength(1);
    expect(statement.payload.assessment_components[0]).toMatchObject({
      skill: "WRITING",
      assessment_origin: "HUMAN",
      subjective_assessment_version_id: versionId,
      rubric_version_id: rubric.id,
      rubric_checksum: rubric.checksum,
    });
    expect(statement.checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.stringify(statement)).not.toMatch(
      /quoted original response supports|PRIVATE SYNTHETIC OFFLINE DRAFT|Liebe Frau Sommer/,
    );
    await logout(page);
  });

  await test.step("candidate reads the exact released human Writing practice statement", async () => {
    await login(page, CANDIDATE);
    await page
      .getByRole("link", { name: "View released results", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: "View result and Statement of Results",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/candidate/results/releases/${releaseId}$`),
    );
    await expect(
      page.getByRole("heading", { name: "Statement of Results", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/not an official provider certificate/i),
    ).toBeVisible();
    await expect(
      page.getByText(result.scoring_run_id, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(statement.checksum, { exact: false }),
    ).toBeVisible();
    await expect(page.getByText(/Writing/).first()).toBeVisible();
    await expect(page.getByText(/HUMAN/).first()).toBeVisible();
    await expect(
      page.getByText(
        /quoted original response supports|PRIVATE SYNTHETIC OFFLINE DRAFT|Liebe Frau Sommer/,
      ),
    ).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await screenshot(page, "writing-candidate-statement-mobile.png");
    await page.setViewportSize({ width: 1280, height: 900 });
    await screenshot(page, "writing-candidate-statement.png");
    await logout(page);
  });

  await test.step("another candidate cannot read the Writing attempt, assessment or statement", async () => {
    const other = await login(page, "candidate@demo.prufai.local");
    const org = other.profile.memberships[0].organisation_id;
    const headers = {
      Authorization: `Bearer ${other.token}`,
      "X-PrufAI-Organisation-ID": org,
    };
    for (const path of [
      `/candidate/exam-attempts/${attemptId}/runtime`,
      `/candidate/releases/${releaseId}`,
      `/candidate/releases/${releaseId}/statement`,
      `/organisations/${org}/subjective-assessments/${assessmentId}`,
    ]) {
      const denied = await page.request.get(`/api/v1${path}`, { headers });
      expect([403, 404]).toContain(denied.status());
      expect(await denied.text()).not.toContain(SUBMITTED_TEXT);
    }
    await page.goto(`/candidate/results/releases/${releaseId}`);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(
      page.getByText(statement.checksum, { exact: false }),
    ).toHaveCount(0);
    await logout(page);
  });

  await test.step("platform audit exposes safe governance references", async () => {
    await login(page, "platform.admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Audit evidence", exact: true })
      .click();
    for (const [event, entityId] of [
      ["rubric.published", rubric.id],
      ["subjective_assessment.human_initialized", versionId],
      ["subjective_assessment.human_proposed", versionId],
      ["subjective_assessment.human_approved", versionId],
      ["scoring.run.completed", result.scoring_run_id],
      ["result.version.created", result.result_version_id],
      ["result.release_created", releaseId],
      ["result.released", result.result_version_id],
      ["statement.created", statement.id],
    ]) {
      await page
        .getByRole("textbox", { name: "Exact event type", exact: true })
        .fill(event);
      const filteredAudit = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("/platform/control-center/audit") &&
          url.searchParams.get("event_type") === event &&
          response.request().method() === "GET"
        );
      });
      await page
        .getByRole("button", { name: "Apply filters", exact: true })
        .click();
      const response = await filteredAudit;
      expect(response.ok()).toBe(true);
      expect(JSON.stringify(await response.json())).not.toMatch(
        /Liebe Frau Sommer|PRIVATE SYNTHETIC OFFLINE DRAFT|quoted original response supports/,
      );
      await expect(
        page
          .getByRole("row")
          .filter({ hasText: event })
          .getByRole("cell", { name: entityId, exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText(
          /PRIVATE SYNTHETIC OFFLINE DRAFT|quoted original response supports|Liebe Frau Sommer/,
        ),
      ).toHaveCount(0);
    }
    await screenshot(page, "writing-platform-safe-audit.png");
    await logout(page);
  });
  expect(attemptId).toBeTruthy();
});

test("candidate has no rubric activation or human Writing review authority in UI or API", async ({
  page,
}) => {
  const candidate = await login(page, CANDIDATE);
  const org = candidate.profile.memberships[0].organisation_id;
  const headers = {
    Authorization: `Bearer ${candidate.token}`,
    "X-PrufAI-Organisation-ID": org,
  };
  for (const route of [
    "/institution/rubrics",
    "/institution/writing-reviews",
  ]) {
    await page.goto(route);
    await expect(
      page.getByRole("heading", { name: "Access restricted", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", {
        name: /Request rubric review|Initialize human Writing assessment|Approve human Writing assessment/,
      }),
    ).toHaveCount(0);
  }
  for (const path of [
    "rubrics",
    "writing-review-options",
    "subjective-assessments",
  ]) {
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/${path}`,
      { headers },
    );
    expect(denied.status()).toBe(403);
  }
  const approval = await page.request.post(
    `/api/v1/organisations/${org}/subjective-assessments/${randomUUID()}/human-approve`,
    {
      headers,
      data: { operation_id: randomUUID(), expected_checksum: "0".repeat(64) },
    },
  );
  expect(approval.status()).toBe(403);
  await logout(page);
});
