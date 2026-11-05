const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, screenshot } = require("./support.cjs");
const {
  actor,
  apiPost,
  clickResponse,
  pin,
} = require("./evaluation-fixture.cjs");
const {
  approvedProposalAndSuite,
  baselineFixture,
  check,
  feedback,
  fillPin,
  loadCandidate,
  mutateCandidate,
  summaries,
} = require("./improvement-fixture.cjs");
const rationale =
  "Synthetic technical request checks passed. Independent empirical assessment evidence is still required.";

test("two real offline request comparisons support independent nonproduction readiness only", async ({
  page,
}) => {
  const { baseline, prompt, canaries, runRequest } =
    await baselineFixture(page);
  const { proposal, suite, owner } = await approvedProposalAndSuite(
    page,
    baseline,
    prompt,
  );
  await page
    .getByRole("link", { name: "Feedback & Improvements", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Feedback view", exact: true })
    .selectOption("proposals");
  await page
    .getByLabel("Exact feedback, pattern or proposal ID", { exact: true })
    .fill(proposal.id);
  await clickResponse(
    page,
    "Load exact receipt",
    `${summaries}/proposals/${proposal.id}`,
    "GET",
    200,
  );
  await page
    .getByRole("link", { name: "Open change candidate workbench", exact: true })
    .click();
  await expect(
    page.getByLabel("Approved proposal version ID", { exact: true }),
  ).toHaveValue(proposal.id);
  await clickResponse(
    page,
    "Load approved proposal",
    `${summaries}/proposals/${proposal.id}`,
    "GET",
    200,
  );
  await fillPin(page, "Published regression suite", suite);
  await fillPin(page, "Exact baseline run", baseline.run);
  const candidate = await clickResponse(
    page,
    "Create draft change candidate",
    `${feedback}/change-candidates`,
    "POST",
    201,
  );
  expect(candidate).toMatchObject({
    proposal: pin(proposal),
    approval: proposal.receipt,
    target: prompt,
    suite_version: pin(suite),
    baseline_run: pin(baseline.run),
    status: "AWAITING_EVALUATION",
    production_readiness: "NOT_READY",
    change_code: "CLEAR_LEGACY_INSTRUCTIONS",
  });
  expect(candidate.draft.id).not.toBe(candidate.target.id);
  expect(candidate.draft.checksum).not.toBe(candidate.target.checksum);
  await logout(page);

  await login(page, actor("examiner-b"));
  const inspected = await loadCandidate(page, candidate.id);
  expect(inspected.allowed_actions).toContain("EVALUATE");
  const evaluated = await mutateCandidate(
    page,
    candidate.id,
    "Evaluate pinned prompt candidate",
    "evaluate",
    "HUMAN_REVIEW_REQUIRED",
  );
  expect(evaluated.evaluation).toMatchObject({
    baseline_run: pin(baseline.run),
    comparison_status: "COMPARABLE",
    technical_status: "PASS",
    empirical_status: "UNCONFIGURED",
    missing_case_count: 0,
  });
  expect(evaluated.production_readiness).toBe("NOT_READY");
  const report = evaluated.comparison.regression;
  expect(report).toMatchObject({
    expected_case_count: 2,
    paired_case_count: 2,
    execution_class: "TEST_ONLY",
  });
  for (const finding of report.case_findings) {
    expect(finding.checks.find(({ code }) => code === check)).toMatchObject({
      baseline: { status: "FAIL", numerator: 0, denominator: 1 },
      candidate: { status: "PASS", numerator: 1, denominator: 1 },
    });
  }
  const measured = page.getByRole("table", {
    name: "Measured regression checks",
    exact: true,
  });
  await expect(measured).toContainText("FAIL · 0 / 1");
  await expect(measured).toContainText("PASS · 1 / 1");
  await expect(
    page.getByRole("table", { name: "Case failure changes", exact: true }),
  ).toContainText(check);
  await expect(
    page.getByRole("button", {
      name: "Record readiness decision",
      exact: true,
    }),
  ).toHaveCount(0);
  await logout(page);

  const independent = await login(page, actor("adjudicator"));
  expect(independent.profile.id).not.toBe(owner.profile.id);
  const reviewable = await loadCandidate(page, candidate.id);
  expect(reviewable.allowed_actions).toContain("REVIEW");
  await page
    .getByRole("combobox", { name: "Readiness decision", exact: true })
    .selectOption("APPROVE_FOR_NONPRODUCTION_VALIDATION");
  await page.getByLabel("Readiness rationale", { exact: true }).fill(rationale);
  const reviewed = await mutateCandidate(
    page,
    candidate.id,
    "Record readiness decision",
    "review",
    "APPROVED_FOR_NONPRODUCTION_VALIDATION",
  );
  expect(reviewed).toMatchObject({
    draft: candidate.draft,
    target: candidate.target,
    production_readiness: "NOT_READY",
    production_activation: false,
  });
  expect(reviewed.history).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        decision: "APPROVE_FOR_NONPRODUCTION_VALIDATION",
        actor_id: independent.profile.id,
      }),
    ]),
  );
  const reloaded = await loadCandidate(page, candidate.id);
  expect(reloaded.receipt).toEqual(reviewed.receipt);
  const detail = page.getByRole("region", {
    name: "Exact change candidate evidence",
    exact: true,
  });
  await expect(detail).toContainText("APPROVE_FOR_NONPRODUCTION_VALIDATION");
  await expect(detail).toContainText("NOT_READY");
  await expect(detail).toContainText("UNCONFIGURED");
  await expect(detail).toContainText(prompt.checksum);
  for (const canary of canaries) {
    expect(JSON.stringify(reloaded)).not.toContain(canary);
    await expect(detail).not.toContainText(canary);
  }
  const baselineStillQualified = await apiPost(
    page,
    independent,
    "/prompt-policy-runs",
    {
      ...runRequest,
      operation_id: randomUUID(),
    },
    201,
  );
  expect(baselineStillQualified).toMatchObject({
    hard_gate: "FAIL",
    failure_count: 2,
  });
  await screenshot(page, "prompt-candidate-nonproduction-readiness.png");
});
