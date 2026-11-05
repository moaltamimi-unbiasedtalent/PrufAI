const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, screenshot } = require("./support.cjs");
const {
  actor,
  apiPost,
  clickResponse,
  headers,
  pin,
} = require("./evaluation-fixture.cjs");
const {
  approvedProposalAndSuite,
  baselineFixture,
  feedback,
  feedbackPost,
  loadCandidate,
  summaries,
} = require("./improvement-fixture.cjs");

const environment = "NONPRODUCTION_VALIDATION";
const productionBlocker = "EMPIRICAL_GATE_UNCONFIGURED";
const selectRationale =
  "The exact successor passed isolated technical checks. Authorize nonproduction validation only.";

function readPath(candidate) {
  return `${summaries}/change-candidates/${candidate.id}/promotion`;
}

async function loadPromotion(page, candidate) {
  const pending = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === readPath(candidate) &&
      response.request().method() === "GET",
  );
  const exact = await loadCandidate(page, candidate.id);
  const response = await pending;
  expect(response.status()).toBe(200);
  return { exact, promotion: await response.json() };
}

async function promoteThroughUi(page, candidate, label, action) {
  const pending = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === readPath(candidate) &&
      response.request().method() === "GET",
  );
  const receipt = await clickResponse(
    page,
    label,
    `${feedback}/change-candidates/${candidate.id}/${action}`,
    "POST",
    200,
  );
  const response = await pending;
  expect(response.status()).toBe(200);
  return { receipt, promotion: await response.json() };
}

async function approvedCandidate(page) {
  const { baseline, prompt, canaries, runRequest } = await baselineFixture(
    page,
    "prompt_promotion_fixture",
  );
  const { proposal, suite, owner } = await approvedProposalAndSuite(
    page,
    baseline,
    prompt,
  );
  const candidate = await feedbackPost(page, owner, "/change-candidates", {
    proposal: pin(proposal),
    approval: proposal.receipt,
    target: { ...prompt, type: "PROMPT_VERSION" },
    suite_version: pin(suite),
    baseline_run: pin(baseline.run),
  });
  await logout(page);

  const evaluator = await login(page, actor("examiner-b"));
  const evaluated = await feedbackPost(
    page,
    evaluator,
    `/change-candidates/${candidate.id}/evaluate`,
    {
      expected_checksum: candidate.receipt.checksum,
    },
    200,
  );
  expect(evaluated.status).toBe("HUMAN_REVIEW_REQUIRED");
  await logout(page);

  const reviewer = await login(page, actor("adjudicator"));
  const decision = await feedbackPost(
    page,
    reviewer,
    `/change-candidates/${candidate.id}/review`,
    {
      expected_checksum: evaluated.checksum,
      decision: "APPROVE_FOR_NONPRODUCTION_VALIDATION",
      rationale:
        "Technical evidence supports isolated validation. Empirical assessment evidence remains unavailable.",
    },
    200,
  );
  expect(decision.status).toBe("APPROVED_FOR_NONPRODUCTION_VALIDATION");
  const reviewOnly = await loadPromotion(page, candidate);
  expect(reviewOnly.promotion.allowed_actions).not.toContain(
    "AUTHORIZE_AND_SELECT",
  );
  await logout(page);
  return {
    candidate,
    decision,
    prompt,
    canaries,
    runRequest,
    excludedActors: [
      owner.profile.id,
      evaluator.profile.id,
      reviewer.profile.id,
    ],
  };
}

test("an independent human selects and reverts an isolated Writing prompt while production is denied", async ({
  page,
}) => {
  const { candidate, decision, prompt, canaries, runRequest, excludedActors } =
    await approvedCandidate(page);
  const release = await login(page, actor("release-authorizer"));
  expect(excludedActors).not.toContain(release.profile.id);
  const { exact, promotion: before } = await loadPromotion(page, candidate);
  expect(exact.evaluation).toMatchObject({
    technical_status: "PASS",
    empirical_status: "UNCONFIGURED",
    missing_case_count: 0,
  });
  expect(before).toMatchObject({
    environment,
    readiness: pin(decision),
    baseline: prompt,
    current_selection: null,
    production_readiness: "NOT_READY",
    production_blocker: productionBlocker,
  });
  const panel = page.getByRole("region", {
    name: "Nonproduction prompt promotion",
    exact: true,
  });
  await panel
    .getByLabel("Promotion rationale", { exact: true })
    .fill(selectRationale);
  const { receipt: selection, promotion: selected } = await promoteThroughUi(
    page,
    candidate,
    "Authorize and select for nonproduction",
    "promotion",
  );
  expect(selection).toMatchObject({
    status: "SELECTED",
    environment,
    actor_id: release.profile.id,
    previous: null,
    selected: { candidate: pin(candidate), prompt: pin(candidate.draft) },
  });
  expect(selected.current_selection).toMatchObject(pin(selection));
  await expect(panel).toContainText(candidate.draft.checksum);
  await logout(page);

  await login(page, actor("examiner-b"));
  const selectedForExecution = await loadPromotion(page, candidate);
  expect(selectedForExecution.promotion.current_selection).toMatchObject(
    pin(selection),
  );
  const { receipt: validation, promotion: validated } = await promoteThroughUi(
    page,
    candidate,
    "Run isolated Writing validation",
    "nonproduction-validations",
  );
  expect(validation).toMatchObject({
    environment,
    status: "COMPLETED",
    execution_class: "TEST_ONLY",
    model_execution: "NOT_RUN",
    selection: pin(selection),
    prompt: pin(candidate.draft),
    case_count: 2,
    production_activation: false,
  });
  expect(validation.cases).toHaveLength(2);
  for (const evidence of validation.cases) {
    expect(evidence.request_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(evidence.output_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(evidence.validated_criteria_count).toBeGreaterThan(0);
  }
  expect(validated.validations).toEqual(
    expect.arrayContaining([expect.objectContaining(pin(validation))]),
  );
  await expect(
    panel.getByRole("table", {
      name: "Isolated Writing validation evidence",
      exact: true,
    }),
  ).toContainText("TEST_ONLY");
  await logout(page);

  const approver = await login(page, actor("release-authorizer"));
  await loadPromotion(page, candidate);
  const denied = await page.request.post(
    `${feedback}/change-candidates/${candidate.id}/promotion`,
    {
      headers: headers(approver),
      data: {
        operation_id: randomUUID(),
        environment: "PRODUCTION",
        readiness: pin(decision),
        expected_selection: pin(selection),
        rationale: "Synthetic production denial acceptance test.",
      },
    },
  );
  expect(denied.status()).toBe(403);
  const denial = await denied.json();
  expect(denial).toMatchObject({
    detail: productionBlocker,
    receipt: {
      status: "DENIED",
      environment: "PRODUCTION",
      reason_code: productionBlocker,
    },
  });
  const afterDenial = await loadPromotion(page, candidate);
  expect(afterDenial.promotion.current_selection).toMatchObject(pin(selection));
  await expect(panel).toContainText(productionBlocker);
  await panel
    .getByLabel("Reversion rationale", { exact: true })
    .fill(
      "Isolated validation completed. Restore the previous empty selection.",
    );
  const { receipt: reversion, promotion: reverted } = await promoteThroughUi(
    page,
    candidate,
    "Revert nonproduction selection",
    "revert",
  );
  expect(reversion).toMatchObject({
    status: "REVERTED",
    environment,
    selected: null,
    previous: { candidate: pin(candidate), prompt: pin(candidate.draft) },
  });
  expect(reverted.current_selection?.selected ?? null).toBeNull();
  expect(reverted.history).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ ...pin(selection), status: "SELECTED" }),
      expect.objectContaining({ ...pin(reversion), status: "REVERTED" }),
    ]),
  );
  expect(pin(reverted.baseline)).toEqual(prompt);
  expect(reverted.production_readiness).toBe("NOT_READY");
  await expect(
    panel.getByRole("table", {
      name: "Nonproduction selection history",
      exact: true,
    }),
  ).toContainText("REVERTED");
  await expect(panel).toContainText(
    "No prompt selected for isolated validation",
  );
  for (const canary of canaries) {
    expect(JSON.stringify(reverted)).not.toContain(canary);
    await expect(panel).not.toContainText(canary);
  }
  const unchanged = await apiPost(
    page,
    approver,
    "/prompt-policy-runs",
    {
      ...runRequest,
      operation_id: randomUUID(),
    },
    201,
  );
  expect(unchanged).toMatchObject({ hard_gate: "FAIL", failure_count: 2 });
  await screenshot(page, "prompt-promotion-isolated-and-reverted.png");
});
