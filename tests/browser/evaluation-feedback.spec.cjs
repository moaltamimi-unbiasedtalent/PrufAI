const { test, expect } = require("@playwright/test");
const { createHash, randomUUID } = require("node:crypto");
const { login, logout, screenshot } = require("./support.cjs");
const {
  actor,
  apiPost,
  clickResponse,
  executeTechnicalRun,
  headers,
  pin,
  publishTechnicalVersions,
  sourceFixture,
} = require("./evaluation-fixture.cjs");

const writes = "/api/v1/platform/feedback";
const reads = "/api/v1/platform/control-center/feedback";
const evaluationSummary = "/api/v1/platform/control-center/evaluation";
const sourceText =
  "Original synthetic feedback fixture. This transcript is not candidate evidence.";
const caseId = "synthetic-feedback-audio-identity";

async function failedFeedbackRun(page) {
  const author = await login(page, actor("source"));
  const fixture = await sourceFixture(page, author);
  const pack = {
    standard_pack_version_id: fixture.source.standard_pack_version_id,
    standard_pack_checksum: fixture.source.standard_pack_checksum,
  };
  const marker = randomUUID().replaceAll("-", "");
  const audioChecksum = createHash("sha256").update(marker).digest("hex");
  const identity = { ...pack, audio_sha256: audioChecksum };
  const dataset = await apiPost(
    page,
    author,
    "/datasets",
    {
      code: `PHASE4_FEEDBACK_FIXTURE_${marker}`,
      assessment_family: "LANGUAGE",
      language: "de",
      cefr_level: null,
      exam_scope: "ORIGINAL SYNTHETIC FEEDBACK FIXTURE",
      skill: "TRANSCRIPTION",
      purpose: "SYNTHETIC_TECHNICAL_REGRESSION",
      origin: "ORIGINAL_SYNTHETIC",
      source_legal_status: "OWNED_ORIGINAL",
      source_reference: "synthetic:phase4-browser-technical-transcription",
      label_provenance: "SYNTHETIC_EXPECTATION",
      expert_review_status: "NOT_EXPERT_REVIEWED",
      retention_classification: "SYNTHETIC_TEST",
      cases: [
        {
          case_id: caseId,
          kind: "TRANSCRIPTION",
          split: "TEST",
          input: {
            language: "de",
            modality: "AUDIO",
            audio_sha256: audioChecksum,
            duration_ms: 1000,
            audio_kind: "TECHNICAL_TONE",
            evidence_identity: identity,
          },
          reference: {
            label_kind: "SYNTHETIC_EXPECTATION",
            text: sourceText,
            language: "de",
            evidence_identity: identity,
          },
          synthetic_observation: {
            text: sourceText,
            language: "de",
            modality: "AUDIO",
            audio_sha256: "0".repeat(64),
            evidence_identity: identity,
          },
        },
      ],
    },
    201,
  );
  await publishTechnicalVersions(
    page,
    author,
    [dataset],
    "synthetic:phase4-independent-fixture-review",
  );
  const evaluator = await login(page, actor("examiner-a"));
  const failed = await executeTechnicalRun(page, evaluator, dataset, pack);
  expect(failed.run).toMatchObject({
    hard_gate: "FAIL",
    execution_class: "TEST_ONLY",
    evidence_class: "TECHNICAL_TEST",
  });
  expect(
    failed.metrics.find(({ name }) => name === "audio_identity_rate"),
  ).toMatchObject({ numerator: 0, denominator: 1 });
  expect(failed.failures).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        case_id: caseId,
        failure_categories: expect.arrayContaining(["AUDIO_IDENTITY_MISMATCH"]),
      }),
    ]),
  );
  await logout(page);
  return failed;
}

async function openFeedback(page, name) {
  const identity = await login(page, actor(name));
  await page
    .getByRole("link", { name: "Feedback & Improvements", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Feedback & Improvements" }),
  ).toBeVisible();
  return identity;
}

async function exactRecord(page, section, id) {
  await page
    .getByRole("combobox", { name: "Feedback view", exact: true })
    .selectOption(section);
  await page
    .getByLabel("Exact feedback, pattern or proposal ID", { exact: true })
    .fill(id);
  return clickResponse(
    page,
    "Load exact receipt",
    `${reads}/${section}/${id}`,
    "GET",
    200,
  );
}

async function decideFeedback(page, item, action, reason) {
  await page
    .getByRole("combobox", { name: "Feedback action", exact: true })
    .selectOption(action);
  await page
    .getByRole("combobox", { name: "Feedback reason", exact: true })
    .selectOption(reason);
  return clickResponse(
    page,
    "Record feedback decision",
    `${writes}/items/${item.id}/decision`,
    "POST",
    200,
  );
}

test("independent feedback governance approves a synthetic improvement for evaluation only", async ({
  page,
}) => {
  const failed = await failedFeedbackRun(page);
  const author = await openFeedback(page, "source");
  await page
    .getByLabel("Source evaluation run ID", { exact: true })
    .fill(failed.run.id);
  const runDetail = await clickResponse(
    page,
    "Load measured failures",
    `${evaluationSummary}/runs/${failed.run.id}`,
    "GET",
    200,
  );
  const pack = {
    type: "STANDARD_PACK_VERSION",
    id: runDetail.pins.standard_pack_version_id,
    checksum: runDetail.pins.standard_pack_checksum,
  };
  const failure = runDetail.failures.find((entry) => entry.case_id === caseId);
  expect(failure.failed_checks).toContain("audio_identity");
  await page
    .getByRole("combobox", { name: "Measured feedback check", exact: true })
    .selectOption({ label: `${caseId} · audio_identity` });
  await page
    .getByRole("combobox", { name: "Feedback category", exact: true })
    .selectOption("TRANSCRIPTION_ERROR");
  await page
    .getByRole("combobox", { name: "Feedback severity", exact: true })
    .selectOption("MEDIUM");
  const item = await clickResponse(
    page,
    "Record feedback",
    `${writes}/items`,
    "POST",
    201,
  );
  expect(item).toMatchObject({
    status: "RECEIVED",
    source_kind: "EVALUATION_FAILURE",
    source_id: failure.result_id,
    source_checksum: failure.result_checksum,
    source_run: pin(failed.run),
    failed_check: "audio_identity",
    evidence_class: "TECHNICAL_TEST",
    requires_reference: true,
    execution_class: "TEST_ONLY",
  });
  await expect(
    page.getByRole("region", { name: "Exact feedback item", exact: true }),
  ).toContainText("EVALUATION_FAILURE");
  expect(JSON.stringify(item)).not.toContain(sourceText);
  await logout(page);

  await openFeedback(page, "reviewer");
  await exactRecord(page, "items", item.id);
  const triaged = await decideFeedback(page, item, "TRIAGE", "SOURCE_REVIEWED");
  expect(triaged.status).toBe("TRIAGED");
  const confirmed = await decideFeedback(page, item, "CONFIRM", "REPRODUCED");
  expect(confirmed.status).toBe("CONFIRMED");
  await logout(page);

  await openFeedback(page, "source");
  await exactRecord(page, "items", item.id);
  const pattern = await clickResponse(
    page,
    "Group confirmed feedback",
    `${writes}/patterns`,
    "POST",
    201,
  );
  expect(pattern.member_count).toBe(1);
  expect(pattern.members).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ id: item.id, checksum: confirmed.checksum }),
    ]),
  );
  await expect(
    page.getByRole("region", { name: "Exact feedback pattern" }),
  ).toContainText(item.id);
  await page
    .getByRole("combobox", { name: "Exact target artifact", exact: true })
    .selectOption(`${pack.type}:${pack.id}`);
  await page
    .getByRole("combobox", { name: "Proposed change", exact: true })
    .selectOption("VALIDATION_GUARD");
  await page
    .getByRole("combobox", { name: "Improvement hypothesis", exact: true })
    .selectOption("PREVENT_RECORDED_FAILURE");
  const proposal = await clickResponse(
    page,
    "Create improvement proposal",
    `${writes}/proposals`,
    "POST",
    201,
  );
  expect(proposal).toMatchObject({
    status: "PENDING_REVIEW",
    target: pack,
    evaluation_requirements: { status: "REQUIRE_EVALUATION" },
    release_readiness: "NOT_READY",
  });
  await expect(
    page.getByRole("button", { name: "Record proposal decision" }),
  ).toHaveCount(0);
  await logout(page);

  const reviewer = await openFeedback(page, "adjudicator");
  expect(reviewer.profile.id).not.toBe(author.profile.id);
  await exactRecord(page, "proposals", proposal.id);
  await page
    .getByRole("combobox", { name: "Proposal action", exact: true })
    .selectOption("APPROVE_FOR_EVALUATION");
  await page
    .getByRole("combobox", { name: "Proposal reason", exact: true })
    .selectOption("EVALUATION_PLAN_SUFFICIENT");
  const approved = await clickResponse(
    page,
    "Record proposal decision",
    `${writes}/proposals/${proposal.id}/decision`,
    "POST",
    200,
  );
  expect(approved).toMatchObject({
    status: "APPROVED_FOR_EVALUATION",
    target: pack,
    evaluation_requirements: { status: "REQUIRE_EVALUATION" },
    release_readiness: "NOT_READY",
  });
  const receipt = page.getByRole("region", {
    name: "Exact improvement proposal",
  });
  await expect(receipt).toContainText("APPROVED_FOR_EVALUATION");
  await expect(receipt).toContainText("REQUIRE_EVALUATION");
  await expect(receipt).toContainText("audio_identity");
  await expect(receipt).toContainText("NOT_READY");
  await expect(receipt).toContainText(pack.checksum);
  await expect(receipt).not.toContainText(sourceText);

  const standards = await page.request.get(
    "/api/v1/platform/control-center/standards?kind=versions&limit=100",
    {
      headers: headers(reviewer),
    },
  );
  expect(standards.status()).toBe(200);
  const target = (await standards.json()).items.find(
    ({ id }) => id === pack.id,
  );
  expect(target.configuration_checksum).toBe(pack.checksum);
  expect(["PUBLISHED", "ACTIVE"]).toContain(target.status);
  await screenshot(page, "feedback-approved-for-evaluation-only.png");
});
