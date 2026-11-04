const { test, expect } = require("@playwright/test");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Technical synthetic microphone capture reaches the actual API, PostgreSQL,
// private MinIO and separate production worker entry point. Deterministic TEST
// ONLY output proves execution plumbing, never speech recognition/proficiency.
// No successful API response, recording, provider result or job is mocked.
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

const CANDIDATE = "transcription.candidate@demo.prufai.local";
const EXAM =
  "TEST ONLY transcription execution practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL";
const PROFILE = "TEST ONLY deterministic transcription — no speech recognition";
const TEST_TEXT =
  "TEST ONLY — technical execution fixture; no speech recognition was performed.";

function headers(actor) {
  return {
    Authorization: `Bearer ${actor.token}`,
    "X-PrufAI-Organisation-ID": actor.profile.memberships.find(
      (item) => item.organisation_slug === "prufai-demo-academy-v2",
    ).organisation_id,
  };
}

function runWorkerOnce() {
  const repository = path.resolve(__dirname, "../..");
  return new Promise((resolve, reject) => {
    const worker = spawn(
      "python3",
      ["-m", "app.speech_transcription.worker", "--once"],
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
    worker.stdout.on("data", (data) => (output += data));
    worker.stderr.on("data", (data) => (output += data));
    const timer = setTimeout(() => {
      worker.kill("SIGTERM");
      reject(
        new Error("The separate transcription worker exceeded 30 seconds."),
      );
    }, 30_000);
    worker.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    worker.on("close", (code) => {
      clearTimeout(timer);
      fs.writeFileSync(
        path.join(
          process.env.PRUFAI_E2E_ARTIFACT_DIR,
          "transcription-worker.log",
        ),
        output,
      );
      if (code === 0) resolve();
      else
        reject(new Error(`The separate transcription worker exited ${code}.`));
    });
  });
}

async function playOriginal(page) {
  const region = page.getByRole("region", {
    name: "Private audio playback",
    exact: true,
  });
  await region
    .getByRole("button", { name: "Load stored audio", exact: true })
    .click();
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

test("a durable separate worker creates exact TEST ONLY derived evidence from genuine private submitted audio without assessment", async ({
  page,
}) => {
  const prohibited = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" &&
      /\/(?:subjective-assessments|scoring-runs|result-versions|releases)(?:\/|$)|\/speaking-reviews\/[^/]+\/(?:human-|propose)|\/score$|\/ai\/.+\/run$/.test(
        pathname,
      )
    )
      prohibited.push(pathname);
  });

  await test.step("institution assigns only the isolated synthetic transcription practice", async () => {
    await login(page, "admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .selectOption({
        label: "Transcription Execution Practice Candidate",
      });
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

  let asset, revisionId, attemptId;
  await test.step("candidate captures and submits actual verified private original audio", async () => {
    const candidate = await login(page, CANDIDATE);
    await page
      .getByRole("link", {
        name: "Prepare exam for exam assignment",
        exact: true,
      })
      .click();
    await page
      .getByRole("button", { name: "Prepare exam", exact: true })
      .click();
    await expect(page).toHaveURL(/\/candidate\/exams\/[0-9a-f-]+$/);
    attemptId = new URL(page.url()).pathname.split("/").pop();
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
    expect((await finalized).status()).toBe(200);
    asset = await (await finalized).json();
    expect(asset.status).toBe("READY");
    expect(asset.validation_provenance.complete_input).toBe(true);
    expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
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
    const org = headers(candidate)["X-PrufAI-Organisation-ID"];
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/candidate-media/${asset.media_asset_id}/transcriptions`,
      { headers: headers(candidate) },
    );
    expect([401, 403]).toContain(denied.status());
    const deniedRequest = await page.request.post(
      `/api/v1/organisations/${org}/candidate-media/${asset.media_asset_id}/transcriptions`,
      {
        headers: headers(candidate),
        data: {
          profile_id: "00000000-0000-4000-8000-000000000001",
          operation_id: "00000000-0000-4000-8000-000000000002",
          language: "de",
          candidate_response_revision_id: revisionId,
          audio_sha256: asset.sha256,
        },
      },
    );
    expect([401, 403]).toContain(deniedRequest.status());
    await screenshot(page, "transcription-candidate-original-submitted.png");
    await logout(page);
  });

  let job, rootId, org, versionId, audioPath;
  await test.step("authorized institution actor queues a pinned TEST ONLY job that survives page reload", async () => {
    const admin = await login(page, "admin@demo.prufai.local");
    org = headers(admin)["X-PrufAI-Organisation-ID"];
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: `Inspect recorded audio ${asset.media_asset_id}`,
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(
        `/institution/speaking-reviews/audio/${asset.media_asset_id}$`,
      ),
    );
    audioPath = new URL(page.url()).pathname;
    await playOriginal(page);
    const panel = page.getByRole("region", {
      name: "Derived transcription evidence",
      exact: true,
    });
    await expect(
      panel.getByText("Transcription status: NOT REQUESTED", { exact: true }),
    ).toBeVisible();
    const profileOptions = panel.getByRole("combobox", {
      name: "Approved transcription profile",
      exact: true,
    });
    const profile = profileOptions
      .locator("option")
      .filter({ hasText: PROFILE });
    await expect(profile).toHaveCount(1);
    await profileOptions.selectOption(await profile.getAttribute("value"));
    await expect(
      panel.getByText(/Provider configuration: TEST_ONLY/),
    ).toBeVisible();
    await panel
      .getByRole("combobox", {
        name: "Declared recording language",
        exact: true,
      })
      .selectOption("de");
    const requested = apiResponse(
      page,
      `/candidate-media/${asset.media_asset_id}/transcriptions`,
      "POST",
    );
    await panel
      .getByRole("button", { name: "Request transcription", exact: true })
      .click();
    const response = await requested;
    expect(response.status()).toBe(201);
    job = await response.json();
    expect(job.status).toBe("QUEUED");
    expect(job.test_only).toBe(true);
    expect(job.candidate_response_revision_id).toBe(revisionId);
    expect(job.audio_sha256).toBe(asset.sha256);
    expect(job.attempt_count).toBe(0);
    rootId = job.transcription_id;
    await expect(
      panel.getByText("Transcription status: QUEUED", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      panel.getByText("Transcription status: QUEUED", { exact: true }),
    ).toBeVisible();
    await expect(
      panel.getByRole("region", {
        name: "Derived machine transcript",
        exact: true,
      }),
    ).toHaveCount(0);
    await screenshot(page, "transcription-queued-durable-job.png");
  });

  await test.step("the executable worker claims actual durable work and persists a new immutable derived version", async () => {
    await runWorkerOnce();
    const panel = page.getByRole("region", {
      name: "Derived transcription evidence",
      exact: true,
    });
    await panel
      .getByRole("button", {
        name: "Refresh transcription status",
        exact: true,
      })
      .click();
    await expect(
      panel.getByText("Transcription status: READY", { exact: true }),
    ).toBeVisible();
    const content = panel.getByRole("region", {
      name: "Derived machine transcript",
      exact: true,
    });
    await expect(content.getByText(TEST_TEXT, { exact: true })).toBeVisible();
    await expect(content.getByText(/TEST ONLY TRANSCRIPT/)).toBeVisible();
    await expect(
      content.getByText("Provider transcript segments", { exact: true }),
    ).toHaveCount(0);
    await screenshot(page, "transcription-ready-derived-evidence.png");
    await logout(page);
  });

  await test.step("an institution reviewer with the actual Exam Manager permissions sees exact derived TEST ONLY provenance and original audio without an assessment being created", async () => {
    const reviewer = await login(page, "reviewer@demo.prufai.local");
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    await expect(page).toHaveURL(/\/institution\/speaking-reviews$/);
    await page
      .getByRole("link", {
        name: `Inspect recorded audio ${asset.media_asset_id}`,
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(new RegExp(`${audioPath}$`));
    const panel = page.getByRole("region", {
      name: "Derived transcription evidence",
      exact: true,
    });
    await expect(
      panel.getByText("Transcription status: READY", { exact: true }),
    ).toBeVisible();
    expect(
      reviewer.profile.memberships.find(
        (membership) => membership.organisation_id === org,
      ).permissions,
    ).toContain("speech_transcription.read");
    expect(
      reviewer.profile.memberships.find(
        (membership) => membership.organisation_id === org,
      ).permissions,
    ).toContain("speech_transcription.request");
    await expect(
      panel.getByRole("button", { name: "Request transcription", exact: true }),
    ).toBeVisible();
    const persisted = await page.request.get(
      `/api/v1/organisations/${org}/speech-transcription-jobs/${job.id}`,
      { headers: headers(reviewer) },
    );
    expect(persisted.status()).toBe(200);
    const ready = await persisted.json();
    expect(ready.status).toBe("READY");
    expect(ready.state_history.map((state) => state.status)).toEqual([
      "QUEUED",
      "RUNNING",
      "READY",
    ]);
    expect(ready.attempt_count).toBe(1);
    expect(ready.started_at).toBeTruthy();
    expect(ready.completed_at).toBeTruthy();
    expect(ready.profile_version).toBe(job.profile_version);
    expect(ready.audio_sha256).toBe(asset.sha256);
    expect(ready.candidate_response_revision_id).toBe(revisionId);
    versionId = ready.transcription_version_id;
    const versions = await page.request.get(
      `/api/v1/organisations/${org}/speech-transcriptions/${rootId}/versions`,
      { headers: headers(reviewer) },
    );
    expect(versions.status()).toBe(200);
    const history = await versions.json();
    expect(history).toHaveLength(1);
    expect(history[0].id).toBe(versionId);
    expect(history[0].execution_job_id).toBe(job.id);
    expect(history[0].candidate_response_revision_id).toBe(revisionId);
    expect(history[0].audio_sha256).toBe(asset.sha256);
    expect(history[0].test_only).toBe(true);
    expect(history[0].transcript).toBe(TEST_TEXT);
    expect(history[0].segments).toEqual([]);
    await expect(
      panel
        .getByRole("region", {
          name: "Derived machine transcript",
          exact: true,
        })
        .getByText(TEST_TEXT, { exact: true }),
    ).toBeVisible();
    await playOriginal(page);
    await screenshot(page, "transcription-reviewer-test-only-evidence.png");
    await logout(page);
  });

  await test.step("platform operations sees safe job metadata but no transcript or private audio access", async () => {
    const platform = await login(page, "platform.admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "AI operations", exact: true })
      .click();
    await expect(page).toHaveURL(/\/control-center\/ai-operations$/);
    await page
      .getByRole("combobox", { name: "Metadata view", exact: true })
      .selectOption("transcriptions");
    const row = page.getByRole("row").filter({ hasText: job.id });
    await expect(row).toBeVisible();
    await expect(row).toContainText("READY");
    await expect(row).toContainText("fixture-transcriber");
    await expect(page.getByText(TEST_TEXT, { exact: true })).toHaveCount(0);
    const denied = await page.request.get(
      `/api/v1/organisations/${org}/speech-transcriptions/${rootId}/versions/${versionId}`,
      {
        headers: {
          Authorization: `Bearer ${platform.token}`,
          "X-PrufAI-Organisation-ID": org,
        },
      },
    );
    expect([401, 403]).toContain(denied.status());
    await screenshot(page, "transcription-platform-metadata-only.png");
  });
  expect(prohibited).toEqual([]);
});
