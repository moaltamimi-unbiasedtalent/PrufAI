const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, screenshot } = require("./support.cjs");

const endpoint = "/api/v1/platform/control-center/evaluation";
const requestFor = (page, kind) =>
  page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === endpoint && url.searchParams.get("kind") === kind;
  });

test("platform Evaluation shows actual safe metadata and honest empty states while institutional access stays denied", async ({
  page,
}) => {
  const platform = await login(page, "platform.admin@demo.prufai.local");
  const datasetsResponse = requestFor(page, "datasets");
  await page.getByRole("link", { name: "Evaluation", exact: true }).click();
  await expect(page).toHaveURL(/\/control-center\/evaluation$/);
  const datasets = await datasetsResponse;
  expect(datasets.status()).toBe(200);
  expect(
    datasets.request().headers()["x-prufai-organisation-id"],
  ).toBeUndefined();
  const datasetPage = await datasets.json();
  expect(datasetPage.limit).toBe(20);
  await expect(
    page.getByRole("heading", { name: "Evaluation", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Technical correctness is separate from German language assessment validity/,
    ),
  ).toBeVisible();
  if (datasetPage.items.length === 0) {
    await expect(
      page.getByText(
        "No governed dataset versions are available in the authorized scope. No data or results are simulated.",
        { exact: true },
      ),
    ).toBeVisible();
  } else {
    await expect(
      page.getByRole("table", { name: "Dataset versions", exact: true }),
    ).toBeVisible();
    for (const item of datasetPage.items) {
      await expect(
        page.getByRole("row").filter({ hasText: item.id }),
      ).toContainText(item.checksum);
    }
  }
  await expect(
    page.getByRole("button", {
      name: /approve|publish|execute|activate|run evaluation/i,
    }),
  ).toHaveCount(0);
  expect(JSON.stringify(datasetPage)).not.toMatch(
    /"(?:cases|private_notes|candidate_text|audio_bytes|credentials|provider_configuration)"\s*:/,
  );

  const code = `UI_SYNTHETIC_DRAFT_${randomUUID().replaceAll("-", "")}`;
  const privateText = "Am Samstag lerne ich Deutsch mit einer Freundin.";
  const identity = { response_revision_id: "1" };
  const createdResponse = await page.request.post(
    "/api/v1/platform/evaluation/datasets",
    {
      headers: {
        Authorization: `Bearer ${platform.token}`,
      },
      data: {
        code,
        assessment_family: "LANGUAGE",
        language: "de",
        cefr_level: null,
        exam_scope: "SYNTHETIC TEST DATA NOT OFFICIAL EXAM MATERIAL",
        skill: "TRANSCRIPTION",
        purpose: "TECHNICAL_UI_PROJECTION",
        origin: "ORIGINAL_SYNTHETIC",
        source_legal_status: "OWNED_ORIGINAL",
        source_reference: "synthetic:browser-evaluation",
        label_provenance: "SYNTHETIC_EXPECTATION",
        expert_review_status: "NOT_EXPERT_REVIEWED",
        retention_classification: "SYNTHETIC_TEST",
        cases: [
          {
            case_id: "original-transcription-arithmetic",
            kind: "TRANSCRIPTION",
            split: "TEST",
            input: {
              language: "de",
              audio_kind: "NONE",
              evidence_identity: identity,
            },
            reference: {
              label_kind: "SYNTHETIC_EXPECTATION",
              text: privateText,
              language: "de",
              evidence_identity: identity,
            },
            synthetic_observation: {
              text: privateText,
              language: "de",
              evidence_identity: identity,
              actions: [],
              tool_calls: [],
            },
          },
        ],
      },
    },
  );
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json();
  const refreshedResponse = requestFor(page, "datasets");
  await page.reload();
  const refreshed = await refreshedResponse;
  expect(refreshed.status()).toBe(200);
  const refreshedPage = await refreshed.json();
  expect(refreshedPage.items.some((item) => item.id === created.id)).toBe(true);
  const draftRow = page.getByRole("row").filter({ hasText: created.id });
  await expect(draftRow).toContainText(code);
  await expect(draftRow).toContainText("DRAFT");
  await expect(draftRow).toContainText(created.checksum);
  expect(JSON.stringify(refreshedPage)).not.toContain(privateText);
  await expect(page.getByText(privateText, { exact: true })).toHaveCount(0);

  const runsResponse = requestFor(page, "runs");
  await page
    .getByRole("combobox", { name: "Evaluation view", exact: true })
    .selectOption("runs");
  const runs = await runsResponse;
  expect(runs.status()).toBe(200);
  const runPage = await runs.json();
  if (runPage.items.length === 0) {
    await expect(
      page.getByText(
        "No evaluation runs are available in the authorized scope. No data or results are simulated.",
        { exact: true },
      ),
    ).toBeVisible();
  } else {
    const run = runPage.items[0];
    const detailResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === `${endpoint}/runs/${run.id}`,
    );
    await page
      .getByRole("button", {
        name: `Inspect evaluation run ${run.id}`,
        exact: true,
      })
      .click();
    expect((await detailResponse).status()).toBe(200);
    await expect(
      page.getByRole("heading", {
        name: "Evaluation run details",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(`Run reference: ${run.id}`, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "Exact pinned configuration",
        exact: true,
      }),
    ).toBeVisible();
  }
  await screenshot(page, "evaluation-safe-platform-metadata.png");
  await logout(page);

  const institution = await login(page, "admin@demo.prufai.local");
  const denied = await page.request.get(endpoint, {
    headers: { Authorization: `Bearer ${institution.token}` },
  });
  expect(denied.status()).toBe(403);
  await page.goto("/control-center/evaluation");
  await expect(
    page.getByRole("heading", { name: "Access denied", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Evaluation", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("table", { name: "Dataset versions", exact: true }),
  ).toHaveCount(0);
});
