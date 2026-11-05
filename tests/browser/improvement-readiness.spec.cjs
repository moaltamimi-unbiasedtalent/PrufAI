const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const { login, logout, screenshot } = require("./support.cjs");
const {
  actor,
  apiPost,
  clickResponse,
  headers,
  pin,
  publishTechnicalVersions,
  sourceFixture,
} = require("./evaluation-fixture.cjs");

const feedback = "/api/v1/platform/feedback";
const summaries = "/api/v1/platform/control-center/feedback";
const candidatePath = "/control-center/feedback/change-candidates";
const check = "prompt_channel_isolation";
const rationale =
  "Synthetic technical request checks passed. Independent empirical assessment evidence is still required.";

async function feedbackPost(page, identity, suffix, data, status = 201) {
  const response = await page.request.post(`${feedback}${suffix}`, {
    headers: headers(identity),
    data: { ...data, operation_id: randomUUID() },
  });
  expect(response.status(), `feedback ${suffix}`).toBe(status);
  return response.json();
}

async function baselineFixture(page) {
  const manifestPath = process.env.PRUFAI_E2E_REFERENCE_MANIFEST;
  if (!manifestPath)
    throw new Error("The guarded evaluation browser manifest is required.");
  const {
    prompt_policy_fixture: { prompt, canaries },
  } = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const author = await login(page, actor("source"));
  const fixture = await sourceFixture(page, author);
  const standard_pack = {
    id: fixture.source.standard_pack_version_id,
    checksum: fixture.source.standard_pack_checksum,
  };
  const dataset = await apiPost(
    page,
    author,
    "/datasets",
    {
      code: `PROMPT_POLICY_BROWSER_${randomUUID().replaceAll("-", "")}`,
      assessment_family: "LANGUAGE",
      language: "de",
      cefr_level: null,
      exam_scope: "ORIGINAL SYNTHETIC PROMPT REQUEST CHECK",
      skill: "WRITING",
      purpose: "SYNTHETIC_TECHNICAL_REGRESSION",
      origin: "ORIGINAL_SYNTHETIC",
      source_legal_status: "OWNED_ORIGINAL",
      source_reference: "synthetic:browser-prompt-policy",
      label_provenance: "SYNTHETIC_EXPECTATION",
      expert_review_status: "NOT_EXPERT_REVIEWED",
      retention_classification: "SYNTHETIC_TEST",
      cases: canaries.map((canary, index) => {
        const identity = { synthetic_fixture_id: `synthetic:${canary}` };
        return {
          case_id: `prompt-policy-case-${index + 1}`,
          kind: "WRITING",
          split: "TEST",
          input: {
            text: canary,
            language: "de",
            modality: "TEXT",
            adversarial: true,
            evidence_identity: identity,
          },
          reference: {
            label_kind: "SYNTHETIC_EXPECTATION",
            language: "de",
            evidence_identity: identity,
            criteria: [{ code: "TASK", max_points: "3", points: null }],
          },
        };
      }),
    },
    201,
  );
  await publishTechnicalVersions(
    page,
    author,
    [dataset],
    "synthetic:prompt-policy-browser-approval",
  );
  const evaluator = await login(page, actor("examiner-a"));
  const runRequest = {
    prompt,
    dataset: pin(dataset),
    standard_pack,
    split: "TEST",
  };
  const run = await apiPost(
    page,
    evaluator,
    "/prompt-policy-runs",
    { ...runRequest, operation_id: randomUUID() },
    201,
  );
  expect(run).toMatchObject({
    hard_gate: "FAIL",
    execution_class: "TEST_ONLY",
    evidence_class: "TECHNICAL_TEST",
    case_count: 2,
    failure_count: 2,
  });
  const baselineResponse = await page.request.get(
    `/api/v1/platform/control-center/evaluation/runs/${run.id}`,
    { headers: headers(evaluator) },
  );
  expect(baselineResponse.status()).toBe(200);
  const baseline = await baselineResponse.json();
  expect(pin(baseline.run)).toEqual(pin(run));
  expect(baseline.failures).toHaveLength(2);
  expect(
    baseline.failures.every(({ failed_checks }) =>
      failed_checks.includes(check),
    ),
  ).toBe(true);
  await logout(page);
  return { baseline, prompt, canaries, runRequest };
}

async function transitionVersion(page, actorIdentity, version, action) {
  const updated = await apiPost(
    page,
    actorIdentity,
    `/regression-versions/${version.id}/${action}`,
    {
      expected_checksum: version.checksum,
      ...(action === "approve"
        ? { review_reference: "synthetic:independent-prompt-regression-review" }
        : {}),
    },
    200,
  );
  expect(updated.status).toBe(
    { submit: "REVIEW_REQUIRED", approve: "APPROVED", publish: "PUBLISHED" }[
      action
    ],
  );
  return updated;
}

async function approvedProposalAndSuite(page, baseline, prompt) {
  const author = await login(page, actor("source"));
  const feedbackItems = [];
  const caseVersions = [];
  for (const failed of baseline.failures) {
    const source = {
      run: pin(baseline.run),
      result: { id: failed.result_id, checksum: failed.result_checksum },
      failed_check: check,
    };
    const received = await feedbackPost(page, author, "/items", {
      source: { kind: "EVALUATION_FAILURE", ...source },
      category: "ACCESS_CONTROL",
      severity: "MEDIUM",
    });
    expect(received).toMatchObject({
      status: "RECEIVED",
      source_kind: "EVALUATION_FAILURE",
      failed_check: check,
      evidence_class: "TECHNICAL_TEST",
      source_run: source.run,
    });
    feedbackItems.push(received);
    const regression = await apiPost(
      page,
      author,
      "/regression-cases",
      {
        operation_id: randomUUID(),
        source_run: source.run,
        source_result: source.result,
        failed_check: check,
      },
      201,
    );
    caseVersions.push(
      await transitionVersion(page, author, regression, "submit"),
    );
  }
  await logout(page);
  const reviewer = await login(page, actor("reviewer"));
  const confirmed = [];
  for (const item of feedbackItems) {
    const triaged = await feedbackPost(
      page,
      reviewer,
      `/items/${item.id}/decision`,
      {
        action: "TRIAGE",
        reason_code: "SOURCE_REVIEWED",
        expected_checksum: item.checksum,
      },
      200,
    );
    const confirmation = await feedbackPost(
      page,
      reviewer,
      `/items/${item.id}/decision`,
      {
        action: "CONFIRM",
        reason_code: "REPRODUCED",
        expected_checksum: triaged.checksum,
      },
      200,
    );
    expect(confirmation.status).toBe("CONFIRMED");
    confirmed.push(confirmation);
  }
  for (const version of caseVersions)
    await transitionVersion(page, reviewer, version, "approve");
  await logout(page);

  const publisher = await login(page, actor("source"));
  for (const version of caseVersions)
    await transitionVersion(page, publisher, version, "publish");
  const pattern = await feedbackPost(page, publisher, "/patterns", {
    anchor: pin(confirmed[0]),
  });
  expect(pattern.member_count).toBe(2);
  expect(pattern.artifact_pins).toContainEqual({
    ...prompt,
    type: "PROMPT_VERSION",
  });
  const proposal = await feedbackPost(page, publisher, "/proposals", {
    pattern: pin(pattern),
    target: { ...prompt, type: "PROMPT_VERSION" },
    change_category: "PROMPT_REVISION",
    hypothesis_code: "PREVENT_RECORDED_FAILURE",
  });
  expect(proposal.status).toBe("PENDING_REVIEW");
  const suite = await apiPost(
    page,
    publisher,
    "/regression-suites",
    {
      operation_id: randomUUID(),
      code: `PROMPT_POLICY_SUITE_${randomUUID().replaceAll("-", "")}`,
      case_pins: caseVersions.map(pin),
      predecessor: null,
    },
    201,
  );
  await transitionVersion(page, publisher, suite, "submit");
  await logout(page);

  const approver = await login(page, actor("reviewer"));
  await transitionVersion(page, approver, suite, "approve");
  const approved = await feedbackPost(
    page,
    approver,
    `/proposals/${proposal.id}/decision`,
    {
      expected_checksum: proposal.receipt.checksum,
      action: "APPROVE_FOR_EVALUATION",
      reason_code: "EVALUATION_PLAN_SUFFICIENT",
    },
    200,
  );
  expect(approved).toMatchObject({
    status: "APPROVED_FOR_EVALUATION",
    release_readiness: "NOT_READY",
  });
  await logout(page);
  const owner = await login(page, actor("source"));
  const published = await transitionVersion(page, owner, suite, "publish");
  expect(published.case_count).toBe(2);
  return { proposal: approved, suite: published, owner };
}

async function loadCandidate(page, id) {
  await page.goto(candidatePath);
  await page.getByLabel("Exact change candidate ID", { exact: true }).fill(id);
  return clickResponse(
    page,
    "Load change candidate",
    `${summaries}/change-candidates/${id}`,
    "GET",
    200,
  );
}

async function mutateCandidate(page, id, label, action, status) {
  const detailPath = `${summaries}/change-candidates/${id}`;
  const detail = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === detailPath &&
      response.request().method() === "GET",
  );
  const receipt = await clickResponse(
    page,
    label,
    `${feedback}/change-candidates/${id}/${action}`,
    "POST",
    200,
  );
  expect(receipt.status).toBe(status);
  const response = await detail;
  expect(response.status()).toBe(200);
  const updated = await response.json();
  expect(updated.receipt).toEqual(pin(receipt));
  return updated;
}

async function fillPin(page, label, record) {
  await page.getByLabel(`${label} ID`, { exact: true }).fill(record.id);
  await page
    .getByLabel(`${label} checksum`, { exact: true })
    .fill(record.checksum);
}

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
