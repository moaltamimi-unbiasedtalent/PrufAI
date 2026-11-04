const { test, expect } = require("@playwright/test");
const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Actual authenticated application/API/PostgreSQL and a separate durable worker.
// The provider is explicitly TEST ONLY; this proves execution/governance, not
// authentic inference, German scoring quality, examiner agreement or fairness.
const CANDIDATE = "ai-writing.candidate@demo.prufai.local";
const EXAM =
  "TEST ONLY AI-assisted B1 Writing proposal practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL";
const TEXT =
  "Liebe Frau Sommer,\nam Dienstag kann ich leider nicht zum Deutschkurs kommen, weil ich einen wichtigen Termin beim Arzt habe. Können Sie mir bitte die Hausaufgaben schicken? Ich möchte zu Hause weiterlernen und die Übungen vor dem nächsten Unterricht machen. Am Donnerstag bin ich wieder im Kurs. Vielen Dank für Ihre Hilfe.\nMit freundlichen Grüßen\nMira Technisches Beispiel";

function institutionHeaders(actor, org) {
  return {
    Authorization: `Bearer ${actor.token}`,
    "X-PrufAI-Organisation-ID": org,
  };
}
function runWritingWorker() {
  const repository = path.resolve(__dirname, "../..");
  return new Promise((resolve, reject) => {
    const child = spawn(
      "python3",
      ["-m", "app.writing_execution.worker", "--once"],
      {
        cwd: path.join(repository, "services/api"),
        env: {
          ...process.env,
          PYTHONPATH: path.join(repository, "services/api"),
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let output = "";
    child.stdout.on("data", (value) => (output += value));
    child.stderr.on("data", (value) => (output += value));
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(
        new Error(
          "The separate Writing worker exceeded its bounded test time.",
        ),
      );
    }, 45_000);
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      fs.writeFileSync(
        path.join(process.env.PRUFAI_E2E_ARTIFACT_DIR, "ai-writing-worker.log"),
        output,
      );
      if (code === 0) resolve();
      else reject(new Error(`The separate Writing worker exited ${code}.`));
    });
  });
}

test("a separate TEST ONLY Writing worker creates a legitimate AI proposal with exact provenance and mandatory pending human review", async ({
  page,
}) => {
  const forbidden = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" &&
      /\/score$|\/result-versions\/.+\/(?:approve|release)|\/releases(?:\/|$)|\/subjective-assessments\/.+\/(?:approve|decision|human-approve|human-proposal|human-decision)$/.test(
        pathname,
      )
    )
      forbidden.push(pathname);
  });
  let candidate, attemptId, source, profile, queued, job, org, requestBody;

  await test.step("institution assigns the isolated explicit AI_ASSISTED technical examination", async () => {
    await login(page, "admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .selectOption({ label: "AI Writing Technical Practice Candidate" });
    const exams = page.getByRole("combobox", {
      name: "Practice exam version",
      exact: true,
    });
    const option = exams.locator("option").filter({ hasText: EXAM });
    await expect(option).toHaveCount(1);
    await exams.selectOption(await option.getAttribute("value"));
    const assigned = apiResponse(page, "/exam-assignments", "POST");
    await page
      .getByRole("button", { name: "Assign assessment", exact: true })
      .click();
    expect((await assigned).status()).toBe(201);
    await logout(page);
  });

  await test.step("candidate saves and submits genuine original synthetic Writing through the runtime", async () => {
    candidate = await login(page, CANDIDATE);
    org = candidate.profile.memberships.find(
      (member) => member.organisation_slug === "prufai-demo-academy-v2",
    ).organisation_id;
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
    await expect(page).toHaveURL(new RegExp(`/candidate/exams/${attemptId}$`));
    await page.getByRole("button", { name: "Start exam", exact: true }).click();
    const text = page.getByRole("textbox", {
      name: "Long response",
      exact: true,
    });
    await text.fill(TEXT);
    await text.press("Tab");
    await expect(
      page.getByText("Saved response revision: 1", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(text).toHaveValue(TEXT);
    const submitted = apiResponse(
      page,
      `/exam-attempts/${attemptId}/submit`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Submit attempt", exact: true })
      .click();
    expect((await submitted).status()).toBe(200);
    await expect(
      page.getByText("Autosave: Submitted", { exact: true }),
    ).toBeVisible();
    await expect(text).toBeDisabled();
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/writing-executions/options`,
      { headers: institutionHeaders(candidate, org) },
    );
    expect([401, 403]).toContain(denied.status());
    await screenshot(page, "ai-writing-submitted-candidate.png");
    await logout(page);
  });

  await test.step("authorized institution queues exact approved evidence and observes durable queued state after reload", async () => {
    await login(page, "admin@demo.prufai.local");
    const available = apiResponse(page, "/writing-executions/options");
    await page
      .getByRole("link", { name: "Writing review", exact: true })
      .click();
    await expect(page).toHaveURL(/\/institution\/writing-reviews$/);
    const response = await available;
    expect(response.status()).toBe(200);
    const options = await response.json();
    source = options.sources.find((entry) => entry.attempt_id === attemptId);
    expect(source).toBeTruthy();
    expect(source.assessment_mode).toBe("AI_ASSISTED");
    expect(source.response_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(source.rubric_checksum).toMatch(/^[a-f0-9]{64}$/);
    profile = options.profiles.find(
      (entry) => entry.test_only && entry.availability === "TEST_ONLY",
    );
    expect(profile).toBeTruthy();
    const panel = page.getByRole("region", {
      name: "AI-assisted Writing proposal execution",
      exact: true,
    });
    await panel
      .getByRole("combobox", {
        name: "AI-assisted submitted Writing response",
        exact: true,
      })
      .selectOption(source.candidate_response_revision_id);
    await panel
      .getByRole("combobox", {
        name: "Approved Writing execution configuration",
        exact: true,
      })
      .selectOption(
        `${profile.execution_profile_id}:${profile.execution_profile_version}`,
      );
    await expect(
      panel.getByText(
        "TEST ONLY executor · no authentic model inference or linguistic scoring accuracy is established.",
        { exact: true },
      ),
    ).toBeVisible();
    const requested = apiResponse(page, "/writing-executions", "POST");
    await panel
      .getByRole("button", {
        name: "Request AI-assisted Writing proposal",
        exact: true,
      })
      .click();
    const request = await requested;
    expect(request.status()).toBe(201);
    requestBody = request.request().postDataJSON();
    queued = await request.json();
    expect(queued.status).toBe("QUEUED");
    expect(queued.assessment_mode).toBe("AI_ASSISTED");
    expect(queued.test_only).toBe(true);
    expect(queued.candidate_response_revision_id).toBe(
      source.candidate_response_revision_id,
    );
    expect(queued.response_checksum).toBe(source.response_checksum);
    expect(queued.execution_profile_version).toBe(
      profile.execution_profile_version,
    );
    expect(queued.review_id).toBeNull();
    expect(requestBody).not.toHaveProperty("ai_run_id");
    expect(requestBody).not.toHaveProperty("proposal");
    await page.reload();
    const persisted = page.getByRole("article", {
      name: `Writing execution ${queued.id}`,
      exact: true,
    });
    await expect(
      persisted.getByText("AI-assisted Writing execution: QUEUED", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      persisted.getByRole("link", { name: /^Inspect AI Writing proposal/ }),
    ).toHaveCount(0);
  });

  await test.step("actual separate worker persists completed run and one legitimate pending proposal through the Writing service", async () => {
    await runWritingWorker();
    await page
      .getByRole("button", {
        name: "Refresh Writing execution status",
        exact: true,
      })
      .click();
    const stored = page.getByRole("article", {
      name: `Writing execution ${queued.id}`,
      exact: true,
    });
    await expect(
      stored.getByText("AI-assisted Writing execution: COMPLETED", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      stored.getByRole("link", { name: /^Inspect AI Writing proposal/ }),
    ).toBeVisible();
    await stored
      .getByText("Persisted Writing execution history", { exact: true })
      .click();
    await expect(
      stored.getByRole("listitem").filter({ hasText: "QUEUED" }),
    ).toHaveCount(1);
    await expect(
      stored.getByRole("listitem").filter({ hasText: "RUNNING" }),
    ).toHaveCount(1);
    await expect(
      stored.getByRole("listitem").filter({ hasText: "COMPLETED" }),
    ).toHaveCount(1);
    const inspection = page.waitForResponse(
      (entry) =>
        new URL(entry.url()).pathname.startsWith(
          `/api/v1/organisations/${org}/subjective-assessments/`,
        ) &&
        !new URL(entry.url()).pathname.endsWith("/versions") &&
        entry.request().method() === "GET",
    );
    await stored
      .getByRole("link", { name: /^Inspect AI Writing proposal/ })
      .click();
    await expect(page).toHaveURL(/\/institution\/writing-reviews\/[0-9a-f-]+$/);
    const inspected = await inspection;
    expect(inspected.status()).toBe(200);
    const proposal = await inspected.json();
    expect(proposal.status).toBe("AI_PROPOSED");
    expect(proposal.assessment_origin).toBe("AI");
    expect(proposal.assessment_mode).toBe("AI_ASSISTED");
    expect(proposal.candidate_text).toBe(TEXT);
    expect(proposal.review.status).toBe("PENDING");
    expect(proposal.review.reviewed_checksum).toBe(proposal.checksum);
    expect(proposal.execution_provenance.execution_status).toBe("COMPLETED");
    expect(proposal.execution_provenance.ai_run_status).toBe("SUCCEEDED");
    expect(proposal.execution_provenance.execution_job_id).toBe(queued.id);
    expect(proposal.execution_provenance.test_only).toBe(true);
    expect(proposal.execution_provenance.response_checksum).toBe(
      source.response_checksum,
    );
    expect(proposal.execution_provenance.rubric_checksum).toBe(
      source.rubric_checksum,
    );
    expect(proposal.execution_provenance.model_version).toBe(
      profile.model_version,
    );
    expect(proposal.execution_provenance.prompt_version_id).toBe(
      profile.prompt_version_id,
    );
    expect(proposal.execution_provenance.agent_version_id).toBe(
      profile.agent_version_id,
    );
    expect(proposal.execution_provenance.workflow_version_id).toBe(
      profile.workflow_version_id,
    );
    job = {
      ...queued,
      assessment_id: proposal.id,
      assessment_version_id: proposal.version_id,
      ai_run_id: proposal.ai_run_id,
      review_id: proposal.review.id,
      checksum: proposal.checksum,
    };
    await screenshot(page, "ai-writing-real-worker-pending-proposal.png");
    await logout(page);
  });

  await test.step("authorized independent reviewer sees exact proposal and can make distinct human choices without automatic approval", async () => {
    const reviewer = await login(page, "reviewer@demo.prufai.local");
    await page
      .getByRole("link", { name: "Writing review", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: `Inspect AI Writing proposal ${job.assessment_id}`,
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/institution/writing-reviews/${job.assessment_id}$`),
    );
    const provenance = page.getByRole("region", {
      name: "Exact AI Writing execution provenance",
      exact: true,
    });
    await expect(provenance.getByText(/TEST ONLY AI PROPOSAL/)).toBeVisible();
    await expect(provenance).toContainText(job.ai_run_id);
    await expect(provenance).toContainText("PENDING");
    await expect(
      page.getByText("Submitted candidate Writing", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(TEXT, { exact: true })).toBeVisible();
    await expect(
      page.getByText("Assessment status: AI_PROPOSED", { exact: true }),
    ).toBeVisible();
    const points = page.getByRole("spinbutton", {
      name: /^Reviewer points for /,
    });
    await expect(points.first()).toHaveValue("");
    await points.first().fill("1");
    await expect(points.first()).toHaveValue("1");
    await expect(
      page.getByRole("button", {
        name: "Approve AI Writing proposal",
        exact: true,
      }),
    ).toBeVisible();
    const response = await page.request.get(
      `/api/v1/organisations/${org}/subjective-assessments/${job.assessment_id}`,
      { headers: institutionHeaders(reviewer, org) },
    );
    expect(response.status()).toBe(200);
    expect((await response.json()).status).toBe("AI_PROPOSED");
    await page.reload();
    await expect(points.first()).toHaveValue("");
    const denied = await page.request.post(
      `/api/v1/organisations/${org}/writing-executions`,
      {
        headers: institutionHeaders(candidate, org),
        data: { ...requestBody, operation_id: randomUUID() },
      },
    );
    expect([401, 403]).toContain(denied.status());
    await screenshot(page, "ai-writing-reviewer-human-decision-required.png");
    await logout(page);
  });

  await test.step("platform sees only safe operational metadata and has no institution proposal access", async () => {
    const platform = await login(page, "platform.admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "AI operations", exact: true })
      .click();
    await expect(page).toHaveURL(/\/control-center\/ai-operations$/);
    await page
      .getByRole("combobox", { name: "Metadata view", exact: true })
      .selectOption("writing-proposals");
    const row = page.getByRole("row").filter({ hasText: queued.id });
    await expect(row).toBeVisible();
    await expect(row).toContainText("COMPLETED");
    await expect(row).toContainText("PENDING");
    await expect(page.getByText(TEXT, { exact: true })).toHaveCount(0);
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/subjective-assessments/${job.assessment_id}`,
      { headers: institutionHeaders(platform, org) },
    );
    expect([400, 401, 403]).toContain(denied.status());
    await screenshot(page, "ai-writing-platform-safe-operations.png");
  });
  expect(forbidden).toEqual([]);
});
