const { test, expect } = require("@playwright/test");
const { randomUUID, createHash } = require("node:crypto");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Technical synthetic microphone evidence only. Chromium generates the media;
// capture, validation, PostgreSQL bindings and private MinIO requests stay real.
// These launch options apply only to this file, not the prior browser journeys.
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

test("synthetic microphone capture persists exact private audio and permits authenticated candidate and reviewer playback", async ({
  page,
  context,
}) => {
  const prohibited = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" &&
      /\/(?:subjective-assessments|speaking-reviews\/[^/]+\/propose|speech-transcriptions|transcriptions|scoring-runs|result-versions|releases)(?:\/|$)/.test(
        path,
      )
    )
      prohibited.push(path);
  });
  await context.addInitScript(() => {
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    window.__prufaiAudioUrls = { created: [], revoked: [] };
    URL.createObjectURL = (blob) => {
      const url = create(blob);
      window.__prufaiAudioUrls.created.push(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      window.__prufaiAudioUrls.revoked.push(url);
      return revoke(url);
    };
  });

  let candidate;
  let attemptId;
  let items;
  const assets = [];
  const candidateEmail = "audio.candidate@demo.prufai.local";
  const examName =
    "Recorded B1 German Audio Practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL";

  function headers(actor) {
    return {
      Authorization: `Bearer ${actor.token}`,
      "X-PrufAI-Organisation-ID":
        actor.profile.memberships.find(
          (member) => member.organisation_slug === "prufai-demo-academy-v2",
        )?.organisation_id ?? actor.profile.memberships[0].organisation_id,
    };
  }
  function flatten(runtime) {
    return runtime.runtime_plan.modules.flatMap((module) =>
      module.sections.flatMap((section) =>
        section.tasks.flatMap((task) => task.items),
      ),
    );
  }
  async function playStored() {
    const player = page.getByRole("region", {
      name: "Private audio playback",
      exact: true,
    });
    await player
      .getByRole("button", { name: "Load stored audio", exact: true })
      .click();
    const audio = player.locator(
      'audio[aria-label="Stored candidate recording"]',
    );
    await expect(audio).toBeVisible();
    await expect
      .poll(() => audio.evaluate((element) => element.readyState))
      .toBeGreaterThanOrEqual(2);
    expect(await audio.getAttribute("src")).toMatch(/^blob:/);
    expect(await audio.getAttribute("src")).not.toMatch(
      /token|Bearer|candidate-audio|s3/,
    );
    await audio.evaluate((element) => element.play());
    await expect
      .poll(() => audio.evaluate((element) => element.currentTime))
      .toBeGreaterThan(0);
    await audio.evaluate((element) => element.pause());
    return await audio.getAttribute("src");
  }
  async function assertRevoked(previousUrls) {
    expect(previousUrls.length).toBeGreaterThan(0);
    await expect
      .poll(() =>
        page.evaluate(
          (urls) =>
            urls.every((url) => window.__prufaiAudioUrls.revoked.includes(url)),
          previousUrls,
        ),
      )
      .toBe(true);
  }
  async function captureCurrent() {
    await page
      .getByRole("button", { name: "Check microphone", exact: true })
      .click();
    await expect(
      page.getByText(
        "Microphone ready. Choose Start recording when you are ready.",
        { exact: true },
      ),
    ).toBeVisible();
    const started = apiResponse(page, "/audio-recordings", "POST");
    await page
      .getByRole("button", { name: "Start recording", exact: true })
      .click();
    const response = await started;
    expect(response.status()).toBe(200);
    const session = await response.json();
    expect(session.policy.submission_behavior).toBe("REQUIRE_ATTACHED");
    expect(session.policy.max_recording_attempts).toBe(2);
    await expect(
      page.getByText("Recording state: RECORDING", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Next question", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Previous question", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Submit attempt", exact: true }),
    ).toBeDisabled();
    // Allow actual encoded audio to accumulate from Chromium's synthetic device.
    await page.waitForTimeout(1800);
    const finalized = apiResponse(
      page,
      `/audio-recordings/${session.recording_session_id}/finalize`,
      "POST",
    );
    const attached = page.waitForResponse(
      (item) =>
        new URL(item.url()).pathname.includes(
          `/exam-attempts/${attemptId}/responses/`,
        ) && item.request().method() === "PUT",
    );
    await page
      .getByRole("button", { name: "Stop and save recording", exact: true })
      .click();
    const finalizedResponse = await finalized;
    expect(finalizedResponse.status()).toBe(200);
    const asset = await finalizedResponse.json();
    expect(asset.status).toBe("READY");
    expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(asset.duration_ms).toBeGreaterThan(0);
    expect(asset.duration_ms).toBeLessThanOrEqual(
      session.policy.max_recording_seconds * 1000,
    );
    expect(asset.byte_size).toBeGreaterThan(0);
    expect(asset.byte_size).toBeLessThanOrEqual(
      session.policy.max_upload_bytes,
    );
    expect(asset.attachment_status).toBe("UPLOADED_NOT_ATTACHED");
    const boundResponse = await attached;
    expect(boundResponse.status()).toBe(200);
    const bound = await boundResponse.json();
    expect(bound.payload.media_asset_id).toBe(asset.media_asset_id);
    expect(bound.revision).toBe(1);
    await expect(
      page.getByText("Recording state: ATTACHED", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Audio saved to this response.", { exact: true }),
    ).toBeVisible();
    assets.push(asset);
    return asset;
  }

  await test.step("institution assigns the original published two-item AUDIO examination", async () => {
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
    expect((await assigned).status()).toBe(201);
    await logout(page);
  });

  await test.step("candidate records, finalizes and authoritatively attaches the first original audio response", async () => {
    candidate = await login(page, candidateEmail);
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
    const runtime = await (await started).json();
    items = flatten(runtime);
    expect(items).toHaveLength(2);
    expect(
      items.every(
        (item) =>
          item.response_mode === "AUDIO" &&
          item.audio_policy.submission_behavior === "REQUIRE_ATTACHED",
      ),
    ).toBe(true);
    await captureCurrent();
    await screenshot(page, "audio-candidate-attached.png");
  });

  await test.step("wrong-item attachment is rejected before a response revision is created", async () => {
    const result = await page.request.put(
      `/api/v1/candidate/exam-attempts/${attemptId}/responses/${items[1].binding_id}`,
      {
        headers: headers(candidate),
        data: {
          payload: { media_asset_id: assets[0].media_asset_id },
          expected_revision: 0,
          client_operation_id: randomUUID(),
        },
      },
    );
    expect([403, 409]).toContain(result.status());
    const runtime = await page.request.get(
      `/api/v1/candidate/exam-attempts/${attemptId}/runtime`,
      { headers: headers(candidate) },
    );
    expect(runtime.status()).toBe(200);
    expect(
      (await runtime.json()).responses.some(
        (response) => response.binding_id === items[1].binding_id,
      ),
    ).toBe(false);
  });

  await test.step("reload retrieves and plays the exact durable first asset with real HTTP byte ranges", async () => {
    await page.reload();
    await expect(
      page.getByText("Audio saved to this response.", { exact: true }),
    ).toBeVisible();
    await playStored();
    const streamPath = `/api/v1/candidate/audio-assets/${assets[0].media_asset_id}/stream`;
    const full = await page.request.get(streamPath, {
      headers: headers(candidate),
    });
    expect(full.status()).toBe(200);
    const fullBytes = await full.body();
    expect(fullBytes.length).toBe(assets[0].byte_size);
    expect(createHash("sha256").update(fullBytes).digest("hex")).toBe(
      assets[0].sha256,
    );
    for (const [range, expectedBytes] of [
      ["bytes=0-63", fullBytes.subarray(0, 64)],
      ["bytes=64-", fullBytes.subarray(64)],
      ["bytes=-64", fullBytes.subarray(-64)],
    ]) {
      const response = await page.request.get(streamPath, {
        headers: { ...headers(candidate), Range: range },
      });
      expect(response.status()).toBe(206);
      expect(await response.body()).toEqual(expectedBytes);
    }
    for (const range of ["bytes=1-2,4-5", `bytes=${fullBytes.length + 100}-`]) {
      const response = await page.request.get(streamPath, {
        headers: { ...headers(candidate), Range: range },
      });
      expect(response.status()).toBe(416);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await screenshot(page, "audio-candidate-playback-mobile.png");
    await page.setViewportSize({ width: 1280, height: 900 });
  });

  await test.step("the second item records independently and the candidate submits both attached responses", async () => {
    const navigated = apiResponse(page, "/navigate", "POST");
    const reloaded = apiResponse(page, `/exam-attempts/${attemptId}/runtime`);
    await page
      .getByRole("button", { name: "Next question", exact: true })
      .click();
    expect((await navigated).status()).toBe(200);
    const runtimeResponse = await reloaded;
    expect(runtimeResponse.status()).toBe(200);
    expect((await runtimeResponse.json()).session.current_binding_id).toBe(
      items[1].binding_id,
    );
    await expect(
      page.getByText("Current question 2", { exact: true }),
    ).toBeVisible();
    await captureCurrent();
    expect(assets[1].media_asset_id).not.toBe(assets[0].media_asset_id);
    await playStored();
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
      page.getByRole("button", { name: "Start recording", exact: true }),
    ).toBeDisabled();
    const created = await page.evaluate(() => [
      ...window.__prufaiAudioUrls.created,
    ]);
    expect(created.length).toBeGreaterThanOrEqual(2);
    await logout(page);
    await assertRevoked(created);
    await expect(page.locator("audio")).toHaveCount(0);
  });

  await test.step("institution reviewer plays the same bound original asset without creating an assessment", async () => {
    await login(page, "reviewer@demo.prufai.local");
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: `Inspect recorded audio ${assets[0].media_asset_id}`,
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", {
        name: "Recorded audio evidence",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(assets[0].sha256, { exact: true }),
    ).toBeVisible();
    await playStored();
    await expect(
      page.getByRole("button", {
        name: /Approve|Submit human assessment|Score submitted/,
      }),
    ).toHaveCount(0);
    await screenshot(page, "audio-reviewer-playback.png");
    const reviewerUrls = await page.evaluate(() => [
      ...window.__prufaiAudioUrls.created,
    ]);
    await logout(page);
    await assertRevoked(reviewerUrls);
  });

  await test.step("another candidate cannot stream, attach, or inspect the original candidate's private audio", async () => {
    const other = await login(page, "candidate@demo.prufai.local");
    const org = headers(other)["X-PrufAI-Organisation-ID"];
    for (const path of [
      `/api/v1/candidate/audio-assets/${assets[0].media_asset_id}/stream`,
      `/api/v1/organisations/${org}/audio-evidence/${assets[0].media_asset_id}`,
    ]) {
      const response = await page.request.get(path, {
        headers: headers(other),
      });
      expect([403, 404]).toContain(response.status());
      expect(await response.text()).not.toContain(assets[0].sha256);
    }
    const attach = await page.request.put(
      `/api/v1/candidate/exam-attempts/${attemptId}/responses/${items[0].binding_id}`,
      {
        headers: headers(other),
        data: {
          payload: { media_asset_id: assets[0].media_asset_id },
          expected_revision: 1,
          client_operation_id: randomUUID(),
        },
      },
    );
    expect([403, 404, 409]).toContain(attach.status());
    await page.goto(
      `/institution/speaking-reviews/audio/${assets[0].media_asset_id}`,
    );
    await expect(
      page.getByRole("heading", { name: "Access restricted", exact: true }),
    ).toBeVisible();
    await expect(page.locator("audio")).toHaveCount(0);
    await logout(page);
  });
  expect(prohibited).toEqual([]);
});
