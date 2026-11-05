const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, screenshot } = require("./support.cjs");

const prefix = "/api/v1/platform/evaluation";
const summary = "/api/v1/platform/control-center/evaluation";
const workbenchPath = "/control-center/evaluation/regressions";
const {
  actor,
  apiPost,
  clickResponse,
  headers,
  pin,
  sourceRuns,
  sourceText,
} = require("./evaluation-fixture.cjs");

async function openWorkbench(page, name) {
  const identity = await login(page, actor(name));
  await page.goto(workbenchPath);
  await expect(
    page.getByRole("heading", { name: "Regression workbench", exact: true }),
  ).toBeVisible();
  return identity;
}

async function loadVersion(page, kind, version) {
  await page
    .getByRole("combobox", { name: "Version kind", exact: true })
    .selectOption(kind);
  await page.getByLabel("Exact version ID", { exact: true }).fill(version.id);
  await clickResponse(
    page,
    "Load regression version",
    `${summary}/regression-${kind}/${version.id}`,
    "GET",
    200,
  );
  await expect(
    page.getByRole("region", { name: "Exact regression version", exact: true }),
  ).toContainText(version.checksum);
}

async function transition(page, version, action, status) {
  await page
    .getByRole("combobox", { name: "Version action", exact: true })
    .selectOption(action);
  if (action === "approve") {
    await page
      .getByLabel("Version review reference", { exact: true })
      .fill("synthetic:phase3-independent-version-review");
  }
  const updated = await clickResponse(
    page,
    "Apply version action",
    `${prefix}/regression-versions/${version.id}/${action}`,
    "POST",
    200,
  );
  expect(updated.status).toBe(status);
  await expect(
    page.getByRole("region", { name: "Exact regression version", exact: true }),
  ).toContainText(status);
  return updated;
}

async function fillPin(page, label, receipt) {
  await page.getByLabel(`${label} ID`, { exact: true }).fill(receipt.id);
  await page
    .getByLabel(`${label} checksum`, { exact: true })
    .fill(receipt.checksum);
}

test("independent reviewers govern a measured synthetic regression and inspect the actual pinned comparison", async ({
  page,
}) => {
  const { failed, passed, caseId } = await sourceRuns(page);
  const author = await login(page, actor("source"));
  await page.getByRole("link", { name: "Evaluation", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Evaluation view", exact: true })
    .selectOption("regressions");
  await page
    .getByRole("link", { name: "Open regression workbench", exact: true })
    .click();
  await page.getByLabel("Source run ID", { exact: true }).fill(failed.run.id);
  await clickResponse(
    page,
    "Load source run",
    `${summary}/runs/${failed.run.id}`,
    "GET",
    200,
  );
  await page
    .getByRole("combobox", { name: "Measured failed check", exact: true })
    .selectOption({ label: `${caseId} · criterion_coverage` });
  const proposed = await clickResponse(
    page,
    "Propose regression case",
    `${prefix}/regression-cases`,
    "POST",
    201,
  );
  expect(proposed.status).toBe("DRAFT");
  expect(proposed.source.run_id).toBe(failed.run.id);
  await transition(page, proposed, "submit", "REVIEW_REQUIRED");
  await apiPost(
    page,
    author,
    `/regression-versions/${proposed.id}/approve`,
    {
      expected_checksum: proposed.checksum,
      review_reference: "synthetic:prohibited-self-approval",
    },
    403,
  );
  await logout(page);

  await openWorkbench(page, "reviewer");
  await loadVersion(page, "cases", proposed);
  await transition(page, proposed, "approve", "APPROVED");
  await logout(page);
  await openWorkbench(page, "source");
  await loadVersion(page, "cases", proposed);
  const publishedCase = await transition(
    page,
    proposed,
    "publish",
    "PUBLISHED",
  );
  await page
    .getByLabel("Suite code", { exact: true })
    .fill(`PHASE3_BROWSER_${randomUUID().replaceAll("-", "")}`);
  await fillPin(page, "Published case 1", publishedCase);
  const draftSuite = await clickResponse(
    page,
    "Draft regression suite",
    `${prefix}/regression-suites`,
    "POST",
    201,
  );
  expect(draftSuite.case_pins).toEqual([
    expect.objectContaining({
      case_version_id: publishedCase.id,
      checksum: publishedCase.checksum,
    }),
  ]);
  await transition(page, draftSuite, "submit", "REVIEW_REQUIRED");
  await logout(page);
  await openWorkbench(page, "reviewer");
  await loadVersion(page, "suites", draftSuite);
  await transition(page, draftSuite, "approve", "APPROVED");
  await logout(page);
  await openWorkbench(page, "source");
  await loadVersion(page, "suites", draftSuite);
  const publishedSuite = await transition(
    page,
    draftSuite,
    "publish",
    "PUBLISHED",
  );
  await logout(page);

  await openWorkbench(page, "examiner-a");
  await fillPin(page, "Published suite version", publishedSuite);
  await fillPin(page, "Baseline run", failed.run);
  await fillPin(page, "Candidate run", passed.run);
  const compared = await clickResponse(
    page,
    "Compare pinned runs",
    `${prefix}/regression-comparisons`,
    "POST",
    201,
  );
  expect(compared.regression).toMatchObject({
    suite_version: pin(publishedSuite),
    baseline_run: pin(failed.run),
    candidate_run: pin(passed.run),
    comparison_status: "COMPARABLE",
    technical_status: "PASS",
    empirical_status: "UNCONFIGURED",
    decision: "UNCONFIGURED",
    expected_case_count: 1,
    paired_case_count: 1,
    missing_baseline_case_ids: [],
    missing_candidate_case_ids: [],
    execution_class: "TEST_ONLY",
    reference_quality_status: "NOT_AVAILABLE",
    production_activation: false,
  });
  const checks = compared.regression.case_findings.flatMap(
    (finding) => finding.checks,
  );
  expect(
    checks.find((check) => check.code === "criterion_coverage"),
  ).toMatchObject({
    baseline: { status: "FAIL", numerator: 0, denominator: 1 },
    candidate: { status: "PASS", numerator: 1, denominator: 1 },
  });
  const measured = page.getByRole("table", {
    name: "Measured regression checks",
    exact: true,
  });
  await expect(measured).toContainText("FAIL · 0 / 1");
  await expect(measured).toContainText("PASS · 1 / 1");
  await expect(
    page.getByRole("button", {
      name: "Record evidence recommendation",
      exact: true,
    }),
  ).toHaveCount(0);
  await logout(page);

  await openWorkbench(page, "adjudicator");
  await page
    .getByLabel("Comparison run ID", { exact: true })
    .fill(compared.run.id);
  const inspected = await clickResponse(
    page,
    "Load regression comparison",
    `${summary}/regression-comparisons/${compared.run.id}`,
    "GET",
    200,
  );
  expect(inspected.allowed_actions).toContain("review");
  await page
    .getByLabel("Comparison review reference", { exact: true })
    .fill("synthetic:phase3-independent-evidence-recommendation");
  await clickResponse(
    page,
    "Record evidence recommendation",
    `${prefix}/regression-comparisons/${compared.run.id}/review`,
    "POST",
    201,
  );
  await expect(
    page.getByRole("table", {
      name: "Recorded evidence recommendations",
      exact: true,
    }),
  ).toContainText("ACCEPTED");
  await expect(
    page.getByText("Release recommendation · NOT_READY", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Return to Evaluation", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Evaluation view", exact: true })
    .selectOption("regressions");
  await expect(
    page
      .getByRole("table", { name: "Regression suites", exact: true })
      .getByRole("row")
      .filter({ hasText: publishedSuite.id }),
  ).toContainText("PUBLISHED");
  const suiteDetail = await clickResponse(
    page,
    `Inspect suite ${publishedSuite.code}`,
    `${summary}/regression-suites/${publishedSuite.id}`,
    "GET",
    200,
  );
  expect(JSON.stringify(suiteDetail)).not.toContain(sourceText);
  expect(suiteDetail.recent_comparisons).toEqual(
    expect.arrayContaining([expect.objectContaining({ id: compared.run.id })]),
  );
  const runDetail = await clickResponse(
    page,
    `Inspect comparison ${compared.run.id}`,
    `${summary}/runs/${compared.run.id}`,
    "GET",
    200,
  );
  expect(runDetail.regression.decision).toBe("UNCONFIGURED");
  expect(JSON.stringify(runDetail)).not.toContain(sourceText);
  expect(JSON.stringify(runDetail)).not.toContain(
    "synthetic:phase3-independent-evidence-recommendation",
  );
  await expect(
    page.getByRole("region", {
      name: "Regression comparison evidence",
      exact: true,
    }),
  ).toContainText("UNCONFIGURED");
  await expect(page.getByText(sourceText, { exact: true })).toHaveCount(0);
  await screenshot(page, "evaluation-dynamic-regression.png");
  await logout(page);

  const candidate = await login(page, "candidate@demo.prufai.local");
  const denied = await page.request.get(
    `${summary}/regression-comparisons/${compared.run.id}`,
    { headers: headers(candidate) },
  );
  expect(denied.status()).toBe(403);
  await page.goto(workbenchPath);
  await expect(
    page.getByRole("heading", { name: "Access denied", exact: true }),
  ).toBeVisible();
});
