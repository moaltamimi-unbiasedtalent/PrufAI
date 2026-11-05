const { test, expect } = require("@playwright/test");
const fs = require("node:fs");
const { randomUUID } = require("node:crypto");
const { login, logout, screenshot } = require("./support.cjs");

const prefix = "/api/v1/platform/evaluation/references";
const safeEndpoint = "/api/v1/platform/control-center/evaluation/references";
const actor = (name) => `evaluation.${name}@demo.prufai.local`;

async function openReference(page, referenceId) {
  const response = page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname === `${prefix}/${referenceId}/workbench`,
  );
  await page.goto(
    `/control-center/evaluation/references?reference=${referenceId}`,
  );
  const result = await response;
  expect(result.status()).toBe(200);
  expect(
    result.request().headers()["x-prufai-organisation-id"],
  ).toBeUndefined();
  const workbench = await result.json();
  await expect(
    page.getByRole("heading", {
      name: "Exact private source evidence",
      exact: true,
    }),
  ).toBeVisible();
  return workbench;
}

async function judgment(page, workbench, points, adjudication = false) {
  expect(workbench.source.origin).toBe("ORIGINAL_SYNTHETIC");
  expect(workbench.reference.evidence_class).toBe("SYNTHETIC_TEST");
  expect(workbench.rubric.criteria.length).toBeGreaterThan(0);
  const level =
    workbench.rubric.performance_levels.find((item) => item.code === "CLEAR")
      ?.code || workbench.rubric.performance_levels[0].code;
  for (const criterion of workbench.rubric.criteria) {
    await page
      .getByLabel(`Points for ${criterion.code}`, { exact: true })
      .fill(String(points));
    await page
      .getByRole("combobox", {
        name: `Performance level for ${criterion.code}`,
        exact: true,
      })
      .selectOption(level);
    await page
      .getByLabel(`Exact response quote for ${criterion.code} (required)`, {
        exact: true,
      })
      .fill(workbench.source.input.text);
    await page
      .getByLabel(`Human rationale for ${criterion.code}`, { exact: true })
      .fill(
        `SYNTHETIC_TEST technical rationale ${points} ${criterion.code}; no language calibration claim.`,
      );
  }
  await page
    .getByLabel("Judgment review operation reference", { exact: true })
    .fill(
      `synthetic:browser-${adjudication ? "adjudication" : "judgment"}-${points}`,
    );
  if (adjudication)
    await page
      .getByLabel("Private adjudication rationale", { exact: true })
      .fill(
        "SYNTHETIC_TEST independent technical reconciliation; both exact original judgment receipts inspected; no empirical quality claim.",
      );
  const action = adjudication ? "adjudications" : "judgments";
  const response = page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname ===
        `${prefix}/${workbench.reference.id}/${action}` &&
      item.request().method() === "POST",
  );
  await page
    .getByRole("button", {
      name: adjudication
        ? "Submit immutable adjudication"
        : "Submit immutable independent judgment",
      exact: true,
    })
    .click();
  const recorded = await response;
  expect(recorded.status()).toBe(201);
  const receipt = await recorded.json();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: `Recorded immutable ${action} receipt:` }),
  ).toContainText(receipt.id);
  return receipt;
}

test("SYNTHETIC_TEST reference uses real authenticated independent judgments, adjudication and private-safe governance metadata", async ({
  page,
}) => {
  if (!process.env.PRUFAI_E2E_REFERENCE_MANIFEST)
    throw new Error(
      "Required synthetic reference manifest is missing; this journey must execute.",
    );
  const manifest = JSON.parse(
    fs.readFileSync(process.env.PRUFAI_E2E_REFERENCE_MANIFEST, "utf8"),
  );
  const referenceId = manifest.reference_id;
  const sourceIdentity = await login(page, actor("source"));
  const selfApproval = await page.request.post(
    `${prefix}/${referenceId}/approve`,
    {
      headers: { Authorization: `Bearer ${sourceIdentity.token}` },
      data: {
        expected_checksum: manifest.reference_checksum,
        review_reference: "synthetic:self-approval-denied",
      },
    },
  );
  expect(selfApproval.status()).toBe(403);
  await logout(page);

  await login(page, actor("examiner-a"));
  const first = await openReference(page, referenceId);
  expect(first.judgments).toHaveLength(0);
  const privateText = first.source.input.text;
  const receiptA = await judgment(page, first, 2);
  await expect(
    page.getByRole("button", {
      name: "Submit immutable independent judgment",
      exact: true,
    }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByText(receiptA.checksum, { exact: false }).first(),
  ).toBeVisible();
  await logout(page);

  await login(page, actor("examiner-b"));
  const second = await openReference(page, referenceId);
  expect(second.reference.judgment_count).toBe(1);
  expect(second.judgments).toHaveLength(0);
  await expect(page.getByText(receiptA.id, { exact: false })).toHaveCount(0);
  const receiptB = await judgment(page, second, 1);
  await logout(page);

  await login(page, actor("adjudicator"));
  const third = await openReference(page, referenceId);
  expect(third.reference.judgment_count).toBe(2);
  expect(new Set(third.judgments.map((item) => item.id))).toEqual(
    new Set([receiptA.id, receiptB.id]),
  );
  for (const receipt of [receiptA, receiptB])
    await expect(
      page.getByRole("heading", {
        name: `Judgment ${receipt.id}`,
        exact: true,
      }),
    ).toBeVisible();
  const adjudication = await judgment(page, third, 2, true);
  await logout(page);
  const sourceOperator = await login(page, actor("source"));
  const managerWorkbench = await openReference(page, referenceId);
  expect(managerWorkbench.judgments).toHaveLength(0);
  expect(managerWorkbench.adjudications).toHaveLength(1);
  expect(managerWorkbench.adjudications[0]).toMatchObject({
    id: adjudication.id,
    checksum: adjudication.checksum,
    body: {},
  });
  expect(managerWorkbench.adjudications[0].body).toEqual({});
  expect(JSON.stringify(managerWorkbench)).not.toContain(
    "SYNTHETIC_TEST technical rationale",
  );
  expect(JSON.stringify(managerWorkbench)).not.toContain(
    "independent technical reconciliation",
  );
  const deriveResponse = page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname ===
        `${prefix}/${referenceId}/dataset-versions` &&
      item.request().method() === "POST",
  );
  await page
    .getByRole("button", {
      name: "Derive draft dataset from exact adjudication",
      exact: true,
    })
    .click();
  const derived = await deriveResponse;
  expect(derived.status()).toBe(201);
  const dataset = await derived.json();
  expect(dataset.status).toBe("DRAFT");
  expect(dataset.evidence_class).not.toBe("EXPERT_LABELLED");
  expect(dataset.evidence_class).not.toBe("PILOT_CALIBRATED");
  const submitted = await page.request.post(
    `/api/v1/platform/evaluation/dataset-versions/${dataset.id}/submit`,
    {
      headers: { Authorization: `Bearer ${sourceOperator.token}` },
      data: { expected_checksum: dataset.checksum },
    },
  );
  expect(submitted.status()).toBe(200);
  expect((await submitted.json()).status).toBe("REVIEW_REQUIRED");
  await page.reload();
  await expect(
    page.getByText(adjudication.id, { exact: false }).first(),
  ).toBeVisible();

  const safeResponse = page.waitForResponse(
    (item) => new URL(item.url()).pathname === safeEndpoint,
  );
  await page
    .getByRole("link", {
      name: "Return to safe Evaluation metadata",
      exact: true,
    })
    .click();
  await page
    .getByRole("combobox", { name: "Evaluation view", exact: true })
    .selectOption("references");
  const safe = await safeResponse;
  expect(safe.status()).toBe(200);
  const metadata = await safe.json();
  const current = metadata.items.find((item) => item.id === referenceId);
  expect(current).toMatchObject({
    evidence_class: "SYNTHETIC_TEST",
    judgment_count: 2,
    adjudication_status: "ADJUDICATED",
    empirical_thresholds: "UNCONFIGURED",
    production_activation: false,
  });
  expect(JSON.stringify(metadata)).not.toContain(privateText);
  expect(JSON.stringify(metadata)).not.toContain(
    "SYNTHETIC_TEST technical rationale",
  );
  await expect(page.getByText(privateText, { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("table", {
      name: "Reference governance metadata",
      exact: true,
    }),
  ).toBeVisible();
  await screenshot(page, "evaluation-synthetic-independent-reference.png");
  await logout(page);

  const reviewer = await login(page, actor("reviewer"));
  const reviewHeaders = { Authorization: `Bearer ${reviewer.token}` };
  const approved = await page.request.post(
    `/api/v1/platform/evaluation/dataset-versions/${dataset.id}/approve`,
    {
      headers: reviewHeaders,
      data: {
        expected_checksum: dataset.checksum,
        provenance_review_reference:
          "synthetic:browser-independent-dataset-review",
      },
    },
  );
  expect(approved.status()).toBe(200);
  expect((await approved.json()).status).toBe("APPROVED");
  const published = await page.request.post(
    `/api/v1/platform/evaluation/dataset-versions/${dataset.id}/publish`,
    { headers: reviewHeaders, data: { expected_checksum: dataset.checksum } },
  );
  expect(published.status()).toBe(200);
  expect((await published.json()).status).toBe("PUBLISHED");
  const calibrated = await page.request.post(
    "/api/v1/platform/evaluation/calibrations",
    {
      headers: reviewHeaders,
      data: {
        operation_id: randomUUID(),
        dataset_version_id: dataset.id,
        dataset_checksum: dataset.checksum,
        comparison_kind: "HUMAN_A_VS_HUMAN_B",
        left_id: receiptA.id,
        left_checksum: receiptA.checksum,
        right_id: receiptB.id,
        right_checksum: receiptB.checksum,
      },
    },
  );
  expect(calibrated.status()).toBe(201);
  const calibration = await calibrated.json();
  expect(calibration.evidence_class).toBe("SYNTHETIC_TEST");
  expect(calibration.execution_class).toBe("TEST_ONLY");
  await page.getByRole("link", { name: "Evaluation", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Evaluation view", exact: true })
    .selectOption("runs");
  const detailsResponse = page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname ===
      `/api/v1/platform/control-center/evaluation/runs/${calibration.id}`,
  );
  await page
    .getByRole("button", {
      name: `Inspect evaluation run ${calibration.id}`,
      exact: true,
    })
    .click();
  const details = await detailsResponse;
  expect(details.status()).toBe(200);
  const computed = await details.json();
  expect(computed.comparison.comparison_kind).toBe("HUMAN_A_VS_HUMAN_B");
  expect(computed.comparison.empirical_thresholds).toBe("UNCONFIGURED");
  expect(computed.comparison.production_activation).toBe(false);
  expect(computed.comparison.criteria.length).toBe(
    third.rubric.criteria.length,
  );
  for (const criterion of computed.comparison.criteria) {
    expect(criterion.disagreement_distribution).toHaveLength(1);
    const distribution = criterion.disagreement_distribution[0];
    expect(Number(distribution.difference)).toBe(-1);
    expect(Number(distribution.absolute_difference)).toBe(1);
    expect(distribution.count).toBe(1);
    expect(distribution.rate).toBe(1);
  }
  const comparison = page.getByRole("region", {
    name: "Exact reference calibration comparison",
    exact: true,
  });
  await expect(comparison).toContainText("HUMAN_A_VS_HUMAN_B");
  await expect(comparison).toContainText("UNCONFIGURED");
  await expect(comparison).toContainText(receiptA.id);
  await expect(comparison).toContainText(receiptB.id);
  const distribution =
    computed.comparison.criteria[0].disagreement_distribution[0];
  await expect(
    page.getByRole("table", {
      name: "Comparison criterion coverage",
      exact: true,
    }),
  ).toContainText(
    `Signed difference ${distribution.difference}; absolute difference ${distribution.absolute_difference}; count 1; rate 1`,
  );
  await expect(comparison).not.toContainText("[object Object]");
  await expect(
    page.getByRole("region", { name: "Evaluation run details", exact: true }),
  ).toContainText("Evidence class: SYNTHETIC_TEST");
  expect(JSON.stringify(computed)).not.toContain(privateText);
  await screenshot(page, "evaluation-synthetic-comparison.png");
  await logout(page);

  const candidate = await login(page, "candidate@demo.prufai.local");
  const denied = await page.request.get(`${prefix}/${referenceId}/workbench`, {
    headers: { Authorization: `Bearer ${candidate.token}` },
  });
  expect(denied.status()).toBe(403);
  await page.goto(
    `/control-center/evaluation/references?reference=${referenceId}`,
  );
  await expect(
    page.getByRole("heading", { name: "Access denied", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(privateText, { exact: true })).toHaveCount(0);
});
