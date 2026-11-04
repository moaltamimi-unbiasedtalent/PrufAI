const { test, expect } = require("@playwright/test");
const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Actual authenticated application/API/PostgreSQL and a separate durable worker.
// The provider is explicitly TEST ONLY; this proves execution/governance, not
// authentic speech understanding, pronunciation, fluency, acoustic relevance or German scoring quality.
const CANDIDATE = "ai-speaking.candidate@demo.prufai.local";
const EXAM =
  "TEST ONLY AI-assisted B1 Speaking proposal practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL";
test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      "--autoplay-policy=no-user-gesture-required",
    ],
  },
});
// Chromium's technical microphone signal contains no assessed candidate speech.
// Complete decoding/storage and this TEST ONLY worker demonstrate exact evidence
// and governance; they do not establish acoustic or linguistic model accuracy.

function institutionHeaders(actor, org) {
  return {
    Authorization: `Bearer ${actor.token}`,
    "X-PrufAI-Organisation-ID": org,
  };
}
function runSpeakingWorker() {
  const repository = path.resolve(__dirname, "../..");
  return new Promise((resolve, reject) => {
    const child = spawn(
      "python3",
      ["-m", "app.speaking_execution.worker", "--once"],
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
          "The separate Speaking worker exceeded its bounded test time.",
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
        path.join(
          process.env.PRUFAI_E2E_ARTIFACT_DIR,
          "ai-speaking-worker.log",
        ),
        output,
      );
      if (code === 0) resolve();
      else reject(new Error(`The separate Speaking worker exited ${code}.`));
    });
  });
}

async function playOriginal(page) {
  const region = page.getByRole("region", {
    name: "Private audio playback",
    exact: true,
  });
  const stored = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/stream") &&
      response.request().method() === "GET",
  );
  await region
    .getByRole("button", { name: "Load stored audio", exact: true })
    .click();
  expect((await stored).status()).toBe(200);
  const audio = region.locator(
    'audio[aria-label="Stored candidate recording"]',
  );
  await expect(audio).toBeVisible();
  await expect
    .poll(() => audio.evaluate((element) => element.readyState))
    .toBeGreaterThanOrEqual(2);
  expect(await audio.getAttribute("src")).toMatch(/^blob:/);
  await audio.evaluate((element) => element.play());
  await expect
    .poll(() => audio.evaluate((element) => element.currentTime))
    .toBeGreaterThan(0);
  await audio.evaluate((element) => element.pause());
}

test("a separate TEST ONLY Speaking worker creates a legitimate AI proposal with exact provenance and mandatory pending human review", async ({
  page,
}) => {
  const forbidden = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" &&
      /\/score$|\/scoring-runs(?:\/|$)|\/result-versions(?:\/|$)|\/(?:releases|certificates)(?:\/|$)|\/candidate-media\/.+\/transcriptions|\/subjective-assessments(?:\/|$)|\/speaking-reviews\/.+\/(?:human-|propose)/.test(
        pathname,
      )
    )
      forbidden.push(pathname);
  });
  let candidate,
    attemptId,
    asset,
    revisionId,
    source,
    profile,
    queued,
    job,
    org,
    requestBody;

  await test.step("institution assigns the isolated explicit AI_ASSISTED technical examination", async () => {
    await login(page, "admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .selectOption({ label: "AI Speaking Technical Practice Candidate" });
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

  await test.step("candidate records, stores, binds and submits actual original private audio through the runtime", async () => {
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
    const started = apiResponse(
      page,
      `/exam-attempts/${attemptId}/start`,
      "POST",
    );
    await page.getByRole("button", { name: "Start exam", exact: true }).click();
    expect((await started).status()).toBe(200);
    await page
      .getByRole("button", { name: "Check microphone", exact: true })
      .click();
    await expect(
      page.getByText(
        "Microphone ready. Choose Start recording when you are ready.",
        { exact: true },
      ),
    ).toBeVisible();
    const recording = apiResponse(page, "/audio-recordings", "POST");
    await page
      .getByRole("button", { name: "Start recording", exact: true })
      .click();
    const session = await (await recording).json();
    await expect(
      page.getByText("Recording state: RECORDING", { exact: true }),
    ).toBeVisible();
    await page.waitForTimeout(1800);
    const finalized = apiResponse(
      page,
      `/audio-recordings/${session.recording_session_id}/finalize`,
      "POST",
    );
    const attached = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname.includes(
          `/exam-attempts/${attemptId}/responses/`,
        ) && response.request().method() === "PUT",
    );
    await page
      .getByRole("button", { name: "Stop and save recording", exact: true })
      .click();
    const savedAudio = await finalized;
    expect(savedAudio.status()).toBe(200);
    asset = await savedAudio.json();
    expect(asset.status).toBe("READY");
    expect(asset.validation_provenance.complete_input).toBe(true);
    expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(asset.duration_ms).toBeGreaterThan(0);
    const saved = await attached;
    expect(saved.status()).toBe(200);
    revisionId = (await saved.json()).candidate_response_revision_id;
    await page.reload();
    await expect(
      page.getByText("Audio saved to this response.", { exact: true }),
    ).toBeVisible();
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
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/speaking-executions/options`,
      { headers: institutionHeaders(candidate, org) },
    );
    expect([401, 403]).toContain(denied.status());
    await screenshot(page, "ai-speaking-submitted-candidate.png");
    await logout(page);
  });

  await test.step("authorized institution queues exact approved evidence and observes durable queued state after reload", async () => {
    await login(page, "admin@demo.prufai.local");
    const available = apiResponse(page, "/speaking-executions/options");
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    await expect(page).toHaveURL(/\/institution\/speaking-reviews$/);
    const response = await available;
    expect(response.status()).toBe(200);
    const options = await response.json();
    source = options.sources.find((entry) => entry.attempt_id === attemptId);
    expect(source).toBeTruthy();
    expect(source.assessment_mode).toBe("AI_ASSISTED");
    expect(source.evidence_mode).toBe("AUDIO");
    expect(source.candidate_response_revision_id).toBe(revisionId);
    expect(source.candidate_media_asset_id).toBe(asset.media_asset_id);
    expect(source.audio_sha256).toBe(asset.sha256);
    expect(source.duration_ms).toBe(asset.duration_ms);
    expect(source.recording_policy_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(source.assessment_policy_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(source.speech_transcription_version_id).toBeNull();
    expect(source.transcript_checksum).toBeNull();
    expect(source.response_checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(source.rubric_checksum).toMatch(/^[a-f0-9]{64}$/);
    profile = options.profiles.find(
      (entry) =>
        entry.test_only &&
        entry.model_name === "speaking-fixture-v1" &&
        entry.availability === "TEST_ONLY",
    );
    expect(profile).toBeTruthy();
    const panel = page.getByRole("region", {
      name: "AI-assisted Speaking proposal execution",
      exact: true,
    });
    await panel
      .getByRole("combobox", {
        name: "AI-assisted submitted Speaking response",
        exact: true,
      })
      .selectOption(source.candidate_response_revision_id);
    await panel
      .getByRole("combobox", {
        name: "Approved Speaking execution configuration",
        exact: true,
      })
      .selectOption(
        `${profile.execution_profile_id}:${profile.execution_profile_version}`,
      );
    await expect(
      panel.getByText(
        "TEST ONLY executor · no authentic model inference or speech understanding, acoustic relevance, pronunciation, fluency or scoring accuracy is established.",
        { exact: true },
      ),
    ).toBeVisible();
    const requested = apiResponse(page, "/speaking-executions", "POST");
    await panel
      .getByRole("button", {
        name: "Request AI-assisted Speaking proposal",
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
    expect(requestBody.candidate_media_asset_id).toBe(asset.media_asset_id);
    expect(requestBody.audio_sha256).toBe(asset.sha256);
    expect(requestBody.duration_ms).toBe(asset.duration_ms);
    expect(requestBody.recording_policy_checksum).toBe(
      source.recording_policy_checksum,
    );
    expect(requestBody.assessment_policy_checksum).toBe(
      source.assessment_policy_checksum,
    );
    expect(requestBody).not.toHaveProperty("storage_key");
    expect(requestBody).not.toHaveProperty("private_url");
    expect(requestBody).not.toHaveProperty("audio_bytes");
    expect(requestBody).not.toHaveProperty("transcript");
    expect(requestBody).not.toHaveProperty("ai_run_id");
    expect(requestBody).not.toHaveProperty("proposal");
    await page.reload();
    const persisted = page.getByRole("article", {
      name: `Speaking execution ${queued.id}`,
      exact: true,
    });
    await expect(
      persisted.getByText("AI-assisted Speaking execution: QUEUED", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      persisted.getByRole("link", { name: /^Inspect AI Speaking proposal/ }),
    ).toHaveCount(0);
  });

  await test.step("actual separate worker persists completed run and one legitimate pending proposal through the Speaking service", async () => {
    await runSpeakingWorker();
    await page
      .getByRole("button", {
        name: "Refresh Speaking execution status",
        exact: true,
      })
      .click();
    const stored = page.getByRole("article", {
      name: `Speaking execution ${queued.id}`,
      exact: true,
    });
    await expect(
      stored.getByText("AI-assisted Speaking execution: COMPLETED", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      stored.getByRole("link", { name: /^Inspect AI Speaking proposal/ }),
    ).toBeVisible();
    await stored
      .getByText("Persisted Speaking execution history", { exact: true })
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
          `/api/v1/organisations/${org}/speaking-reviews/`,
        ) &&
        !new URL(entry.url()).pathname.endsWith("/versions") &&
        entry.request().method() === "GET",
    );
    await stored
      .getByRole("link", { name: /^Inspect AI Speaking proposal/ })
      .click();
    await expect(page).toHaveURL(
      /\/institution\/speaking-reviews\/[0-9a-f-]+$/,
    );
    const inspected = await inspection;
    expect(inspected.status()).toBe(200);
    const proposal = await inspected.json();
    expect(proposal.status).toBe("AI_PROPOSED");
    expect(proposal.assessment_origin).toBe("AI");
    expect(proposal.assessment_mode).toBe("AI_ASSISTED");
    expect(proposal.original_audio_primary_evidence).toBe(true);
    expect(proposal.derived_transcription).toBe(false);
    expect(proposal.evidence_mode).toBe("AUDIO");
    expect(proposal.candidate_media_asset_id).toBe(asset.media_asset_id);
    expect(proposal.audio_sha256).toBe(asset.sha256);
    expect(proposal.candidate_response_revision_id).toBe(revisionId);
    expect(proposal.speech_transcription_version_id).toBeNull();
    expect(proposal.transcript_checksum).toBeNull();
    expect(proposal.approved_score).toBeNull();
    expect(proposal.criteria.length).toBeGreaterThan(0);
    for (const criterion of proposal.criteria) {
      expect(criterion.approved_points).toBeNull();
      expect(criterion.approved_level).toBeNull();
      expect(criterion.evidence.length).toBeGreaterThan(0);
      for (const span of criterion.evidence) {
        expect(span.candidate_media_asset_id ?? span.asset_id).toBe(
          asset.media_asset_id,
        );
        expect(span.audio_sha256).toBe(asset.sha256);
        expect(span.start_ms).toBeGreaterThanOrEqual(0);
        expect(span.end_ms).toBeGreaterThan(span.start_ms);
        expect(span.end_ms).toBeLessThanOrEqual(asset.duration_ms);
      }
    }
    expect(proposal.execution_provenance.candidate_media_asset_id).toBe(
      asset.media_asset_id,
    );
    expect(proposal.execution_provenance.audio_sha256).toBe(asset.sha256);
    expect(proposal.execution_provenance.duration_ms).toBe(asset.duration_ms);
    expect(proposal.execution_provenance.recording_policy_checksum).toBe(
      source.recording_policy_checksum,
    );
    expect(proposal.execution_provenance.assessment_policy_checksum).toBe(
      source.assessment_policy_checksum,
    );
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
      assessment_id: proposal.assessment_id,
      assessment_version_id: proposal.version_id,
      ai_run_id: proposal.ai_run_id,
      review_id: proposal.review.id,
      checksum: proposal.checksum,
    };
    await screenshot(page, "ai-speaking-real-worker-pending-proposal.png");
    await logout(page);
  });

  await test.step("authorized independent reviewer sees exact proposal and can make distinct human choices without automatic approval", async () => {
    const reviewer = await login(page, "reviewer@demo.prufai.local");
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: `Inspect AI Speaking proposal ${job.assessment_id}`,
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/institution/speaking-reviews/${job.assessment_id}$`),
    );
    const provenance = page.getByRole("region", {
      name: "Exact AI Speaking execution provenance",
      exact: true,
    });
    await expect(provenance.getByText(/TEST ONLY AI PROPOSAL/)).toBeVisible();
    await expect(provenance).toContainText(job.ai_run_id);
    await expect(provenance).toContainText("PENDING");
    await expect(
      page.getByText("Original audio — primary evidence", { exact: true }),
    ).toBeVisible();
    await expect(provenance).toContainText(
      "No transcript was used or requested",
    );
    await playOriginal(page);
    await expect(
      page.getByText(/^Assessment status:\s*AI_PROPOSED$/),
    ).toBeVisible();
    const points = page.getByRole("spinbutton", {
      name: /^Points for /,
    });
    await expect(points.first()).toHaveValue("");
    await points.first().fill("1");
    await expect(points.first()).toHaveValue("1");
    await expect(
      page.getByRole("button", {
        name: "Approve AI Speaking proposal",
        exact: true,
      }),
    ).toBeVisible();
    const response = await page.request.get(
      `/api/v1/organisations/${org}/speaking-reviews/${job.assessment_id}`,
      { headers: institutionHeaders(reviewer, org) },
    );
    expect(response.status()).toBe(200);
    const pending = await response.json();
    expect(pending.status).toBe("AI_PROPOSED");
    expect(pending.review.status).toBe("PENDING");
    expect(pending.approved_score).toBeNull();
    await page.reload();
    await expect(points.first()).toHaveValue("");
    const denied = await page.request.post(
      `/api/v1/organisations/${org}/speaking-executions`,
      {
        headers: institutionHeaders(candidate, org),
        data: { ...requestBody, operation_id: randomUUID() },
      },
    );
    expect([401, 403]).toContain(denied.status());
    await screenshot(page, "ai-speaking-reviewer-human-decision-required.png");
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
      .selectOption("speaking-proposals");
    const row = page.getByRole("row").filter({ hasText: queued.id });
    await expect(row).toBeVisible();
    await expect(row).toContainText("COMPLETED");
    await expect(row).toContainText("PENDING");
    await expect(page.getByText(asset.sha256, { exact: true })).toHaveCount(0);
    for (const privateField of [
      "storage_key",
      "private_url",
      "audio_bytes",
      "transcript",
      "prompt",
    ])
      expect(await row.textContent()).not.toContain(privateField);
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/speaking-reviews/${job.assessment_id}`,
      { headers: institutionHeaders(platform, org) },
    );
    expect([400, 401, 403]).toContain(denied.status());
    await screenshot(page, "ai-speaking-platform-safe-operations.png");
  });
  expect(forbidden).toEqual([]);
});
