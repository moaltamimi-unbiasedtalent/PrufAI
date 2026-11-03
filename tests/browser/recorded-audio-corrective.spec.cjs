const { test, expect } = require("@playwright/test");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Synthetic Chromium microphone only. All successful API, PostgreSQL and S3
// operations remain real. Negative headers are understood only by the isolated
// browser acceptance launcher, never by the production application.
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
const candidateEmail = "audio.candidate@demo.prufai.local";
const examName = "Recorded B1 German Audio Practice";

async function prepareOriginalAudioExam(page) {
  await login(page, "admin@demo.prufai.local");
  await page
    .getByRole("link", { name: "Assign practice assessment", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Candidate", exact: true })
    .selectOption({ label: "Recorded Audio Practice Candidate" });
  const exam = page.getByRole("combobox", {
    name: "Practice exam version",
    exact: true,
  });
  const option = exam.locator("option").filter({ hasText: examName });
  await expect(option).toHaveCount(1);
  await exam.selectOption(await option.getAttribute("value"));
  const assigned = apiResponse(page, "/exam-assignments", "POST");
  await page
    .getByRole("button", { name: "Assign assessment", exact: true })
    .click();
  const response = await assigned;
  expect(response.status()).toBe(201);
  const assignment = await response.json();
  await logout(page);
  const actor = await login(page, candidateEmail);
  await page.goto(`/candidate/exams/assignment-${assignment.id}`);
  await page.getByRole("button", { name: "Prepare exam", exact: true }).click();
  await expect(page).toHaveURL(/\/candidate\/exams\/[0-9a-f-]+$/);
  const attemptId = new URL(page.url()).pathname.split("/").pop();
  const started = apiResponse(
    page,
    `/exam-attempts/${attemptId}/start`,
    "POST",
  );
  await page.getByRole("button", { name: "Start exam", exact: true }).click();
  expect((await started).status()).toBe(200);
  await expect(
    page.getByRole("button", { name: "Check microphone", exact: true }),
  ).toBeEnabled();
  return { actor, attemptId };
}

async function startCapture(page, requireVisibleRecording = true) {
  await page
    .getByRole("button", { name: "Check microphone", exact: true })
    .click();
  await expect(
    page.getByText(
      "Microphone ready. Choose Start recording when you are ready.",
      { exact: true },
    ),
  ).toBeVisible();
  const response = apiResponse(page, "/audio-recordings", "POST");
  await page
    .getByRole("button", { name: "Start recording", exact: true })
    .click();
  const started = await response;
  expect(started.status()).toBe(200);
  const recording = await started.json();
  expect(recording.recording_remaining_ms).toBeGreaterThan(0);
  expect(recording.recording_remaining_ms).toBeLessThanOrEqual(30_000);
  if (requireVisibleRecording)
    await expect(
      page.getByText("Recording state: RECORDING", { exact: true }),
    ).toBeVisible();
  return recording;
}

async function stopCapture(page, recording, expected = 200) {
  const finalized = apiResponse(
    page,
    `/audio-recordings/${recording.recording_session_id}/finalize`,
    "POST",
  );
  await page
    .getByRole("button", { name: "Stop and save recording", exact: true })
    .click();
  const response = await finalized;
  expect(response.status()).toBe(expected);
  return response;
}

async function expectPending(page) {
  for (const name of [
    "Next question",
    "Previous question",
    "Submit attempt",
    "Start recording",
  ])
    await expect(
      page.getByRole("button", { name, exact: true }),
    ).toBeDisabled();
  await expect(
    page.getByText("Audio saved to this response.", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText(
      "Finish or cancel the pending audio recording and attachment before navigating or submitting.",
      { exact: true },
    ),
  ).toBeVisible();
}

async function navigateSecond(page, attemptId) {
  const navigated = apiResponse(page, "/navigate", "POST");
  const runtime = apiResponse(page, `/exam-attempts/${attemptId}/runtime`);
  await page
    .getByRole("button", { name: "Next question", exact: true })
    .click();
  expect((await navigated).status()).toBe(200);
  expect((await runtime).status()).toBe(200);
  await expect(
    page.getByText("Current question 2", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check microphone", exact: true }),
  ).toBeEnabled();
}

test("reloaded staged audio retains its interlock through a real recovery failure, then attaches or cancels authoritatively", async ({
  page,
}) => {
  const { attemptId } = await prepareOriginalAudioExam(page);
  await page.route(
    "**/api/v1/candidate/audio-recordings/*/finalize",
    async (route) => {
      await route.continue({
        headers: {
          ...route.request().headers(),
          "x-prufai-test-fault": "receipt-after-put-once",
        },
      });
    },
  );
  const recording = await startCapture(page);
  await page.waitForTimeout(1800); // Actual synthetic microphone bytes, not a successful response fixture.
  await stopCapture(page, recording, 503);
  await page.reload();
  await expect(
    page.getByText("Recording state: UNFINISHED", { exact: true }),
  ).toBeVisible();
  await expectPending(page);
  let failRecovery = true;
  await page.route(
    "**/api/v1/candidate/audio-recordings/*/recover",
    async (route) => {
      if (failRecovery) {
        failRecovery = false;
        await route.continue({
          headers: {
            ...route.request().headers(),
            "x-prufai-test-fault": "recover-storage-once",
          },
        });
      } else await route.continue();
    },
  );
  const failed = apiResponse(
    page,
    `/audio-recordings/${recording.recording_session_id}/recover`,
    "POST",
  );
  await page
    .getByRole("button", { name: "Recover stored upload", exact: true })
    .click();
  expect((await failed).status()).toBe(503);
  await expect(
    page.getByText("Recording state: FAILED", { exact: true }),
  ).toBeVisible();
  await expectPending(page);
  const recovered = apiResponse(
    page,
    `/audio-recordings/${recording.recording_session_id}/recover`,
    "POST",
  );
  await page
    .getByRole("button", { name: "Recover stored upload", exact: true })
    .click();
  const response = await recovered;
  expect(response.status()).toBe(200);
  const evidence = await response.json();
  expect(evidence.recording_session_id).toBe(recording.recording_session_id);
  expect(evidence.asset.status).toBe("READY");
  await expect(
    page.getByText("Recording state: UPLOADED_NOT_ATTACHED", { exact: true }),
  ).toBeVisible();
  await expectPending(page);
  const attached = page.waitForResponse(
    (item) =>
      item.request().method() === "PUT" &&
      item.url().includes(`/exam-attempts/${attemptId}/responses/`),
  );
  await page
    .getByRole("button", { name: "Attach uploaded audio", exact: true })
    .click();
  const bound = await attached;
  expect(bound.status()).toBe(200);
  expect((await bound.json()).payload.media_asset_id).toBe(
    evidence.asset.media_asset_id,
  );
  await expect(
    page.getByText("Audio saved to this response.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Next question", exact: true }),
  ).toBeEnabled();
  await screenshot(page, "audio-recovered-and-attached.png");

  await navigateSecond(page, attemptId);
  const cancelledRecording = await startCapture(page);
  await page.waitForTimeout(1800);
  await stopCapture(page, cancelledRecording, 503);
  await page.reload();
  await expect(
    page.getByText("Recording state: UNFINISHED", { exact: true }),
  ).toBeVisible();
  await expectPending(page);
  const cancelled = apiResponse(
    page,
    `/audio-recordings/${cancelledRecording.recording_session_id}/cancel`,
    "POST",
  );
  await page
    .getByRole("button", { name: "Cancel unfinished recording", exact: true })
    .click();
  const cancellation = await cancelled;
  expect(cancellation.status()).toBe(200);
  expect((await cancellation.json()).status).toBe("CANCELLED");
  await expect(
    page.getByText("Recording state: IDLE", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Previous question", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Submit attempt", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Check microphone", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByText("Audio saved to this response.", { exact: true }),
  ).toHaveCount(0);
  await page.unroute("**/api/v1/candidate/audio-recordings/*/finalize");
  const valid = await startCapture(page);
  expect(valid.attempt_number).toBe(2);
  await page.waitForTimeout(1800);
  await stopCapture(page, valid);
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
  await logout(page);
});

for (const offset of [-60_000, 60_000]) {
  test(`recording obeys its server allowance with a ${offset / 1000} second client wall-clock skew`, async ({
    page,
    context,
  }) => {
    await context.addInitScript((skew) => {
      const actual = Date.now.bind(Date);
      Date.now = () => actual() + skew;
    }, offset);
    await prepareOriginalAudioExam(page);
    const recording = await startCapture(page);
    const remaining = page
      .locator("p")
      .filter({ hasText: /^Server recording time remaining:/ });
    await expect(remaining).toHaveText(
      /Server recording time remaining: (?:2[0-9]|30) seconds/,
    );
    await page.waitForTimeout(1800);
    await expect(
      page.getByText("Recording state: RECORDING", { exact: true }),
    ).toBeVisible();
    await stopCapture(page, recording);
    await expect(
      page.getByText("Audio saved to this response.", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByText("Audio saved to this response.", { exact: true }),
    ).toBeVisible();
    await logout(page);
  });
}

test("a delayed recording event rechecks elapsed monotonic time instead of extending the allowance", async ({
  page,
}) => {
  await prepareOriginalAudioExam(page);
  const recording = await startCapture(page);
  await page.waitForTimeout(1800);
  const finalized = apiResponse(
    page,
    `/audio-recordings/${recording.recording_session_id}/finalize`,
    "POST",
  );
  await page.evaluate(() => {
    const actual = performance.now.bind(performance);
    Object.defineProperty(performance, "now", {
      configurable: true,
      value: () => actual() + 40_000,
    });
    window.dispatchEvent(new Event("pageshow"));
  });
  expect((await finalized).status()).toBe(200);
  await expect(
    page.getByText("Audio saved to this response.", { exact: true }),
  ).toBeVisible();
  await logout(page);
});

test("capture stops at an actual shortened server expiry while the client wall clock is behind", async ({
  page,
  context,
}) => {
  await context.addInitScript(() => {
    const actual = Date.now.bind(Date);
    Date.now = () => actual() - 60_000;
  });
  await prepareOriginalAudioExam(page);
  await page.route(
    "**/api/v1/candidate/attempts/*/items/*/audio-recordings",
    async (route) => {
      await route.continue({
        headers: {
          ...route.request().headers(),
          "x-prufai-test-fault": "recording-expiry-soon",
        },
      });
    },
  );
  const finalized = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/candidate\/audio-recordings\/[^/]+\/finalize$/.test(
        new URL(response.url()).pathname,
      ),
  );
  const recording = await startCapture(page, false);
  expect(recording.recording_remaining_ms).toBeLessThanOrEqual(1500);
  // No Stop click: the server-issued allowance must expire independently of the
  // local wall clock. The test launcher only shortened a real persisted expiry.
  const response = await finalized;
  expect(response.status()).toBe(200);
  const asset = await response.json();
  expect(asset.duration_ms).toBeGreaterThan(0);
  expect(asset.duration_ms).toBeLessThan(5000);
  await expect(
    page.getByText("Audio saved to this response.", { exact: true }),
  ).toBeVisible();
  await logout(page);
});
