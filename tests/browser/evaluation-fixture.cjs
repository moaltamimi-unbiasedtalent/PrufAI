const { expect } = require("@playwright/test");
const { createHash, randomUUID } = require("node:crypto");
const fs = require("node:fs");
const { login, logout } = require("./support.cjs");

const prefix = "/api/v1/platform/evaluation";
const sourceText =
  "Ich schreibe diesen ursprünglichen Brief für den technischen Regressionstest.";
const actor = (name) => `evaluation.${name}@demo.prufai.local`;
const pin = ({ id, checksum }) => ({ id, checksum });
const headers = (identity) => ({ Authorization: `Bearer ${identity.token}` });

async function apiPost(page, identity, suffix, data, status) {
  const response = await page.request.post(`${prefix}${suffix}`, {
    headers: headers(identity),
    data,
  });
  expect(response.status()).toBe(status);
  return response.json();
}

async function clickResponse(page, button, path, method, status) {
  const result = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === path &&
      response.request().method() === method,
  );
  await page.getByRole("button", { name: button, exact: true }).click();
  const response = await result;
  expect(response.status()).toBe(status);
  return response.json();
}

async function sourceFixture(page, author) {
  const manifestPath = process.env.PRUFAI_E2E_REFERENCE_MANIFEST;
  if (!manifestPath)
    throw new Error("The guarded evaluation browser manifest is required.");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const reference = await page.request.get(
    `${prefix}/references/${manifest.reference_id}/workbench`,
    {
      headers: headers(author),
    },
  );
  expect(reference.status()).toBe(200);
  const fixture = await reference.json();
  expect(fixture.reference.evidence_class).toBe("SYNTHETIC_TEST");
  return fixture;
}

async function sourceRuns(page) {
  const author = await login(page, actor("source"));
  const fixture = await sourceFixture(page, author);
  expect(fixture.rubric.criteria.length).toBeGreaterThan(1);
  const configurationPins = {
    standard_pack_version_id: fixture.source.standard_pack_version_id,
    standard_pack_checksum: fixture.source.standard_pack_checksum,
    rubric_version_id: fixture.source.rubric_version_id,
    rubric_checksum: fixture.source.rubric_checksum,
  };
  const identity = {
    ...configurationPins,
    response_checksum: createHash("sha256").update(sourceText).digest("hex"),
  };
  const caseId = "synthetic-writing-criterion-coverage";
  const observations = fixture.rubric.criteria.map((criterion) => ({
    criterion_code: criterion.code,
    proposed_points: String(Math.min(1, Number(criterion.max_points))),
    evidence: [{ start: 0, end: 3, quote: "Ich" }],
  }));
  const immutableCase = {
    case_id: caseId,
    kind: "WRITING",
    split: "TEST",
    input: {
      text: sourceText,
      language: "de",
      modality: "TEXT",
      evidence_identity: identity,
    },
    reference: {
      label_kind: "SYNTHETIC_EXPECTATION",
      language: "de",
      criteria: fixture.rubric.criteria.map((criterion) => ({
        code: criterion.code,
        max_points: String(criterion.max_points),
        points: null,
      })),
      evidence_identity: identity,
    },
  };
  const draft = {
    code: `PHASE3_FIXTURE_${randomUUID().replaceAll("-", "")}`,
    assessment_family: "LANGUAGE",
    language: "de",
    cefr_level: null,
    exam_scope: "ORIGINAL SYNTHETIC REGRESSION FIXTURE",
    skill: "WRITING",
    purpose: "SYNTHETIC_TECHNICAL_REGRESSION",
    origin: "ORIGINAL_SYNTHETIC",
    source_legal_status: "OWNED_ORIGINAL",
    source_reference: "synthetic:phase3-browser-technical-writing",
    label_provenance: "SYNTHETIC_EXPECTATION",
    expert_review_status: "NOT_EXPERT_REVIEWED",
    retention_classification: "SYNTHETIC_TEST",
  };
  function dataset(criteria) {
    return {
      ...draft,
      cases: [
        {
          ...immutableCase,
          synthetic_observation: {
            modality: "TEXT",
            evidence_identity: identity,
            criteria,
            actions: [],
            tool_calls: [],
          },
        },
      ],
    };
  }
  const baseline = await apiPost(
    page,
    author,
    "/datasets",
    dataset(observations.slice(1)),
    201,
  );
  const candidate = await apiPost(
    page,
    author,
    `/datasets/${baseline.dataset_id}/versions`,
    dataset(observations),
    201,
  );
  expect(baseline.checksum).not.toBe(candidate.checksum);
  expect(candidate.dataset_id).toBe(baseline.dataset_id);
  await publishTechnicalVersions(
    page,
    author,
    [baseline, candidate],
    "synthetic:phase3-browser-fixture-approval",
  );
  const evaluator = await login(page, actor("examiner-a"));
  const failed = await executeTechnicalRun(
    page,
    evaluator,
    baseline,
    configurationPins,
  );
  const passed = await executeTechnicalRun(
    page,
    evaluator,
    candidate,
    configurationPins,
  );
  expect(failed.run.hard_gate).toBe("FAIL");
  expect(passed.run.hard_gate).toBe("PASS");
  expect(failed.run.execution_class).toBe("TEST_ONLY");
  expect(passed.run.execution_class).toBe("TEST_ONLY");
  expect(failed.failures).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        case_id: caseId,
        failure_categories: expect.arrayContaining([
          "MISSING_OR_UNKNOWN_CRITERIA",
        ]),
      }),
    ]),
  );
  expect(
    failed.metrics.find((metric) => metric.name === "criterion_coverage_rate"),
  ).toMatchObject({ numerator: 0, denominator: 1 });
  expect(
    passed.metrics.find((metric) => metric.name === "criterion_coverage_rate"),
  ).toMatchObject({ numerator: 1, denominator: 1 });
  await logout(page);
  return { failed, passed, caseId };
}

async function publishTechnicalVersions(
  page,
  author,
  versions,
  reviewReference,
) {
  for (const version of versions) {
    const submitted = await apiPost(
      page,
      author,
      `/dataset-versions/${version.id}/submit`,
      { expected_checksum: version.checksum },
      200,
    );
    expect(submitted.status).toBe("REVIEW_REQUIRED");
  }
  await logout(page);
  const reviewer = await login(page, actor("reviewer"));
  for (const version of versions) {
    const approved = await apiPost(
      page,
      reviewer,
      `/dataset-versions/${version.id}/approve`,
      {
        expected_checksum: version.checksum,
        provenance_review_reference: reviewReference,
      },
      200,
    );
    expect(approved.status).toBe("APPROVED");
    const published = await apiPost(
      page,
      reviewer,
      `/dataset-versions/${version.id}/publish`,
      { expected_checksum: version.checksum },
      200,
    );
    expect(published.status).toBe("PUBLISHED");
  }
  await logout(page);
}

function executeTechnicalRun(page, evaluator, version, configurationPins) {
  return apiPost(
    page,
    evaluator,
    "/runs",
    {
      operation_id: randomUUID(),
      dataset_version_id: version.id,
      dataset_checksum: version.checksum,
      configuration: {
        ...configurationPins,
        dataset_split: "TEST",
        execution_adapter: "SYNTHETIC_OBSERVATION_REPLAY_V1",
        evaluation_method: "DETERMINISTIC_REFERENCE_COMPARISON",
        evaluation_method_version: "1",
      },
    },
    201,
  );
}

module.exports = {
  actor,
  apiPost,
  clickResponse,
  executeTechnicalRun,
  headers,
  pin,
  publishTechnicalVersions,
  sourceFixture,
  sourceRuns,
  sourceText,
};
