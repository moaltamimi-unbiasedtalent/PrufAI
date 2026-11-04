const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Technical synthetic microphone capture is recorded by the actual browser,
// decoded and stored privately by the actual API. Diagnostic human judgments
// exercise governance and deterministic scoring, not language proficiency.
// No successful API response, recording, assessment, score or release is mocked.
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

const ADMIN = "admin@demo.prufai.local";
const REVIEWER = "reviewer@demo.prufai.local";
const CANDIDATE = "speaking.results.candidate@demo.prufai.local";
const PENDING = "speaking.results.pending@demo.prufai.local";
const EXAM =
  "Human-only B1 German Speaking Results Practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL";
const PRIVATE =
  "PRIVATE SPEAKING RESULTS RATIONALE: diagnostic judgment over technical synthetic capture; no linguistic proficiency or CEFR accuracy claim.";

function headers(actor) {
  return {
    Authorization: `Bearer ${actor.token}`,
    "X-PrufAI-Organisation-ID":
      actor.profile.memberships.find(
        (item) => item.organisation_slug === "prufai-demo-academy-v2",
      )?.organisation_id ?? actor.profile.memberships[0].organisation_id,
  };
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

async function assign(page, name) {
  const admin = await login(page, ADMIN);
  await page
    .getByRole("link", { name: "Assign practice assessment", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "Candidate", exact: true })
    .selectOption({ label: name });
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
  return admin;
}

async function recordAndSubmit(page, email) {
  const candidate = await login(page, email);
  await page
    .getByRole("link", {
      name: "Prepare exam for exam assignment",
      exact: true,
    })
    .click();
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
  const upload = await finalized;
  expect(upload.status()).toBe(200);
  const asset = await upload.json();
  expect(asset.status).toBe("READY");
  expect(asset.duration_ms).toBeGreaterThan(0);
  expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(asset.validation_provenance.complete_input).toBe(true);
  const saved = await attached;
  expect(saved.status()).toBe(200);
  const response = await saved.json();
  const bindingId = new URL(saved.url()).pathname.split("/").pop();
  await page.reload();
  await expect(
    page.getByText("Audio saved to this response.", { exact: true }),
  ).toBeVisible();
  await playOriginal(page);
  const submitted = apiResponse(
    page,
    `/exam-attempts/${attemptId}/submit`,
    "POST",
  );
  await page
    .getByRole("button", { name: "Submit attempt", exact: true })
    .click();
  expect((await submitted).status()).toBe(200);
  const earlyResults = await page.request.get("/api/v1/candidate/releases", {
    headers: headers(candidate),
  });
  expect(earlyResults.status()).toBe(200);
  expect(await earlyResults.json()).toEqual([]);
  await expect(
    page.getByRole("button", { name: "Start recording", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Submit attempt", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("alert").filter({ hasText: "Autosave: Submitted" }),
  ).toBeVisible();
  const rerecord = await page.request.post(
    `/api/v1/candidate/attempts/${attemptId}/items/${bindingId}/audio-recordings`,
    { headers: headers(candidate), data: { operation_id: randomUUID() } },
  );
  expect(rerecord.status()).toBe(409);
  expect((await rerecord.json()).detail).toMatch(/not writable/);
  await screenshot(
    page,
    email === CANDIDATE
      ? "speaking-results-candidate-submitted.png"
      : "speaking-results-pending-submitted.png",
  );
  await logout(page);
  return {
    candidate,
    attemptId,
    bindingId,
    asset,
    revisionId: response.candidate_response_revision_id,
  };
}

function assignment(page, name) {
  return page.getByRole("article").filter({ hasText: name });
}

test("recorded Speaking completes independent human approval, scoring, release and a private practice statement", async ({
  page,
}) => {
  const prohibited = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" &&
      /\/(?:speech-transcriptions|transcriptions)(?:\/|$)|\/ai\/.+\/run$|\/speaking-reviews\/[^/]+\/propose$/.test(
        path,
      )
    )
      prohibited.push(path);
  });
  let rubric,
    rubricPath,
    state,
    assessmentId,
    versionId,
    proposed,
    institution,
    run,
    result,
    releaseId,
    statement;

  await test.step("an institution Admin publishes the exact Speaking rubric after an independent approval", async () => {
    await login(page, ADMIN);
    await page
      .getByRole("link", { name: "Writing rubrics", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: "Inspect rubric speaking-results-b1-v1",
        exact: true,
      })
      .click();
    await expect(
      page.getByText("Status: DRAFT", { exact: true }),
    ).toBeVisible();
    rubricPath = new URL(page.url()).pathname;
    const requested = apiResponse(page, "/request-review", "POST");
    await page
      .getByRole("button", { name: "Request rubric review", exact: true })
      .click();
    expect((await requested).status()).toBe(200);
    await expect(
      page.getByRole("button", {
        name: "Approve reviewed rubric",
        exact: true,
      }),
    ).toHaveCount(0);
    await logout(page);
    await login(page, REVIEWER);
    await page.goto(rubricPath);
    const approved = apiResponse(page, "/approve", "POST");
    await page
      .getByRole("button", { name: "Approve reviewed rubric", exact: true })
      .click();
    expect((await approved).status()).toBe(200);
    await expect(
      page.getByText("Status: APPROVED", { exact: true }),
    ).toBeVisible();
    await logout(page);
    await login(page, ADMIN);
    await page.goto(rubricPath);
    const published = apiResponse(page, "/publish", "POST");
    await page
      .getByRole("button", { name: "Publish approved rubric", exact: true })
      .click();
    const response = await published;
    expect(response.status()).toBe(200);
    rubric = await response.json();
    expect(rubric.skill).toBe("SPEAKING");
    expect(rubric.ai_assessment_policy).toMatchObject({
      mode: "HUMAN_ONLY",
      enabled: false,
    });
    expect(rubric.evidence_policy.mode).toBe("AUDIO");
    await logout(page);
  });

  await test.step("candidate records, persists and submits original private audio without a premature released score", async () => {
    await assign(page, "Speaking Results Practice Candidate");
    state = await recordAndSubmit(page, CANDIDATE);
  });

  await test.step("Reviewer A listens and submits exact audio-based criteria, but cannot self-approve", async () => {
    const assessor = await login(page, REVIEWER);
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    await page
      .getByRole("combobox", {
        name: "Submitted Speaking response",
        exact: true,
      })
      .selectOption(state.revisionId);
    await page
      .getByRole("combobox", { name: "Published Speaking rubric", exact: true })
      .selectOption(rubric.id);
    const initialized = apiResponse(
      page,
      "/subjective-assessments/human-speaking",
      "POST",
    );
    await page
      .getByRole("button", {
        name: "Initialize human Speaking assessment",
        exact: true,
      })
      .click();
    const response = await initialized;
    expect(response.status()).toBe(200);
    const created = await response.json();
    assessmentId = created.id;
    versionId = created.version_id;
    await playOriginal(page);
    for (const [code, points, level] of [
      ["TASK", "3", "CLEAR"],
      ["LANGUAGE", "2", "DEVELOPING"],
    ]) {
      await page
        .getByRole("spinbutton", { name: `Points for ${code}`, exact: true })
        .fill(points);
      await page
        .getByRole("combobox", {
          name: `Performance level for ${code}`,
          exact: true,
        })
        .selectOption(level);
      await page
        .getByRole("spinbutton", {
          name: `Audio start ms for ${code} span 1`,
          exact: true,
        })
        .fill("0");
      await page
        .getByRole("spinbutton", {
          name: `Audio end ms for ${code} span 1`,
          exact: true,
        })
        .fill(String(Math.min(state.asset.duration_ms, 1000)));
      await page
        .getByRole("textbox", {
          name: `Private human rationale for ${code}`,
          exact: true,
        })
        .fill(PRIVATE);
    }
    const proposal = apiResponse(page, "/human-proposal", "POST");
    await page
      .getByRole("button", {
        name: "Submit human Speaking assessment for independent review",
        exact: true,
      })
      .click();
    const proposalResponse = await proposal;
    expect(proposalResponse.status()).toBe(200);
    proposed = await proposalResponse.json();
    expect(proposed.status).toBe("IN_REVIEW");
    await expect(
      page.getByRole("button", {
        name: "Approve human Speaking assessment",
        exact: true,
      }),
    ).toHaveCount(0);
    const self = await page.request.post(
      `/api/v1/organisations/${headers(assessor)["X-PrufAI-Organisation-ID"]}/speaking-reviews/${versionId}/human-approve`,
      {
        headers: headers(assessor),
        data: {
          operation_id: randomUUID(),
          expected_checksum: proposed.checksum,
        },
      },
    );
    expect(self.status()).toBe(403);
    await logout(page);
  });

  await test.step("Reviewer B independently approves; Admin follows the exact attempt to the existing scorer", async () => {
    institution = await login(page, ADMIN);
    await page.goto(`/institution/speaking-reviews/${assessmentId}`);
    await playOriginal(page);
    const approved = apiResponse(page, "/human-approve", "POST");
    await page
      .getByRole("button", {
        name: "Approve human Speaking assessment",
        exact: true,
      })
      .click();
    expect((await approved).status()).toBe(200);
    await expect(
      page.locator("p").filter({ hasText: /^Assessment status:/ }),
    ).toContainText("APPROVED");
    await page
      .getByRole("link", {
        name: "Inspect submitted attempt and scoring →",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/institution/overview#attempt-${state.attemptId}$`),
    );
    const target = assignment(page, "Speaking Results Practice Candidate");
    await expect(
      target.getByRole("heading", {
        name: "Speaking scoring readiness: READY",
        exact: true,
      }),
    ).toBeVisible();
    const scored = apiResponse(
      page,
      `/exam-attempts/${state.attemptId}/score`,
      "POST",
    );
    await target
      .getByRole("button", { name: "Score submitted attempt", exact: true })
      .click();
    const response = await scored;
    expect(response.status()).toBe(200);
    run = await response.json();
    await target
      .getByRole("link", {
        name: "Inspect score, review and release →",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/\/institution\/results\/[0-9a-f-]+$/);
    const org = headers(institution)["X-PrufAI-Organisation-ID"];
    const resultId = new URL(page.url()).pathname.split("/").pop();
    const versions = await page.request.get(
      `/api/v1/organisations/${org}/results/${resultId}/versions`,
      { headers: headers(institution) },
    );
    expect(versions.status()).toBe(200);
    result = (await versions.json()).sort(
      (a, b) => b.version_number - a.version_number,
    )[0];
    expect(result.scoring_run_id).toBe(run.id);
    expect(result.status).toBe("REVIEW_REQUIRED");
    expect(result.outcome).toBe("UNDETERMINED");
    expect(Number(result.raw_awarded_points)).toBe(5);
    expect(Number(result.raw_maximum_points)).toBe(6);
    expect(result.assessment_components).toHaveLength(1);
    expect(result.assessment_components[0]).toMatchObject({
      skill: "SPEAKING",
      assessment_origin: "HUMAN",
      assessment_mode: "HUMAN_ONLY",
      evidence_mode: "AUDIO",
      subjective_assessment_version_id: versionId,
      rubric_version_id: rubric.id,
      rubric_checksum: rubric.checksum,
      candidate_response_revision_id: state.revisionId,
    });
    await expect(
      page.getByRole("heading", { name: "Speaking assessment", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Evidence mode: AUDIO", { exact: true }),
    ).toBeVisible();
    const replay = await page.request.post(
      `/api/v1/organisations/${org}/exam-attempts/${state.attemptId}/score`,
      { headers: headers(institution) },
    );
    expect(replay.status()).toBe(200);
    expect((await replay.json()).id).toBe(run.id);
    await screenshot(page, "speaking-results-scoring-run.png");
  });

  await test.step("human result approval, release and statement issuance are separate explicit actions", async () => {
    const org = headers(institution)["X-PrufAI-Organisation-ID"];
    const premature = await page.request.post(
      `/api/v1/organisations/${org}/result-versions/${result.result_version_id}/release`,
      {
        headers: headers(institution),
        data: { operation_id: randomUUID() },
      },
    );
    expect(premature.status()).toBe(409);
    const requested = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/request-review`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Request human result review", exact: true })
      .click();
    expect((await requested).status()).toBe(200);
    const approved = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/approve`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Approve reviewed result", exact: true })
      .click();
    expect((await approved).status()).toBe(200);
    const released = apiResponse(
      page,
      `/result-versions/${result.result_version_id}/release`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Release approved result", exact: true })
      .click();
    const response = await released;
    expect(response.status()).toBe(200);
    releaseId = (await response.json()).release_id;
    const releaseReplay = await page.request.post(
      `/api/v1/organisations/${org}/result-versions/${result.result_version_id}/release`,
      {
        headers: headers(institution),
        data: response.request().postDataJSON(),
      },
    );
    expect(releaseReplay.status()).toBe(200);
    expect((await releaseReplay.json()).release_id).toBe(releaseId);
    await expect(page).toHaveURL(
      new RegExp(`/institution/releases/${releaseId}$`),
    );
    const issued = apiResponse(
      page,
      `/releases/${releaseId}/statement`,
      "POST",
    );
    await page
      .getByRole("button", { name: "Issue Statement of Results", exact: true })
      .click();
    const artifact = await issued;
    expect(artifact.status()).toBe(200);
    statement = await artifact.json();
    expect(statement.checksum).toMatch(/^[a-f0-9]{64}$/);
    expect(statement.payload).toMatchObject({
      classification: "PRACTICE",
      result_version_id: result.result_version_id,
      scoring_run_id: run.id,
      standard_pack_version_id: rubric.standard_pack_version_id,
      attempt_id: state.attemptId,
      candidate_user_id: state.candidate.profile.id,
      issued_by_user_id: institution.profile.id,
    });
    expect(statement.payload.assessment_components[0]).toMatchObject({
      skill: "SPEAKING",
      assessment_origin: "HUMAN",
      assessment_mode: "HUMAN_ONLY",
      evidence_mode: "AUDIO",
      subjective_assessment_version_id: versionId,
    });
    expect(JSON.stringify(statement)).not.toMatch(
      /PRIVATE SPEAKING RESULTS RATIONALE|storage_key|private_url|audio_sha256|candidate_media_asset_id|transcript|ai_proposal/,
    );
    await logout(page);
  });

  await test.step("candidate reads the exact released practice Speaking score and statement", async () => {
    await login(page, CANDIDATE);
    await page
      .getByRole("link", { name: "View released results", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: "View result and Statement of Results",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/candidate/results/releases/${releaseId}$`),
    );
    await expect(
      page.getByText("Classification: PRACTICE", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Assessment: Speaking", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Speaking assessment", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Assessment origin: HUMAN", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Evidence mode: AUDIO", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/Raw score:\s*5(?:\.0+)?\s*\/\s*6(?:\.0+)?/),
    ).toBeVisible();
    await expect(page.getByText(run.id, { exact: true })).toBeVisible();
    await expect(
      page.getByText(statement.checksum, { exact: false }),
    ).toBeVisible();
    await expect(
      page.getByText(/not an official provider certificate/i),
    ).toBeVisible();
    await expect(page.getByText(PRIVATE, { exact: false })).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await screenshot(page, "speaking-results-candidate-statement-mobile.png");
    await page.setViewportSize({ width: 1280, height: 900 });
    await logout(page);
    const other = await login(page, "candidate@demo.prufai.local");
    for (const suffix of ["", "/statement"]) {
      const denied = await page.request.get(
        `/api/v1/candidate/releases/${releaseId}${suffix}`,
        { headers: headers(other) },
      );
      expect([403, 404]).toContain(denied.status());
      expect(await denied.text()).not.toContain(statement.checksum);
    }
    await page.goto(`/candidate/results/releases/${releaseId}`);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(
      page.getByText(statement.checksum, { exact: false }),
    ).toHaveCount(0);
    await logout(page);
  });

  await test.step("platform oversight exposes safe original-audio and scoring/release references", async () => {
    await login(page, "platform.admin@demo.prufai.local");
    await page
      .getByRole("link", { name: "Audit evidence", exact: true })
      .click();
    for (const [event, entityId] of [
      ["rubric.published", rubric.id],
      ["candidate_audio.finalized", state.asset.media_asset_id],
      ["subjective_assessment.human_approved", versionId],
      ["scoring.run.completed", run.id],
      ["result.version.created", result.result_version_id],
      ["result.release_created", releaseId],
      ["statement.created", statement.id],
    ]) {
      await page
        .getByRole("textbox", { name: "Exact event type", exact: true })
        .fill(event);
      const filtered = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("/platform/control-center/audit") &&
          url.searchParams.get("event_type") === event &&
          response.request().method() === "GET"
        );
      });
      await page
        .getByRole("button", { name: "Apply filters", exact: true })
        .click();
      const response = await filtered;
      expect(response.status()).toBe(200);
      const payload = await response.json();
      const row = payload.items.find((item) => item.entity_id === entityId);
      expect(row).toBeTruthy();
      expect(JSON.stringify(payload)).not.toMatch(
        /PRIVATE SPEAKING RESULTS RATIONALE|storage_key|private_url|audio_bytes|transcript|ai_proposal/,
      );
      await expect(
        page
          .getByRole("row")
          .filter({ hasText: event })
          .getByRole("cell", { name: entityId, exact: true }),
      ).toBeVisible();
      if (event === "candidate_audio.finalized") {
        expect(JSON.stringify(row.evidence_references)).toContain(
          state.asset.media_asset_id,
        );
        expect(JSON.stringify(row.evidence_references)).toContain(
          state.asset.sha256,
        );
        await expect(
          page.getByText(state.asset.sha256, { exact: false }),
        ).toBeVisible();
      }
    }
    await screenshot(page, "speaking-results-platform-safe-audit.png");
    await logout(page);
  });
  expect(prohibited).toEqual([]);
});

test("unapproved recorded Speaking cannot produce or release a complete score and unauthorized actors are denied", async ({
  page,
}) => {
  await assign(page, "Speaking Pending Practice Candidate");
  const state = await recordAndSubmit(page, PENDING);
  const admin = await login(page, ADMIN);
  const org = headers(admin)["X-PrufAI-Organisation-ID"];
  await page.goto(`/institution/overview#attempt-${state.attemptId}`);
  const target = assignment(page, "Speaking Pending Practice Candidate");
  await expect(
    target.getByRole("heading", {
      name: "Speaking scoring readiness: INCOMPLETE",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    target.getByRole("button", {
      name: "Score submitted attempt",
      exact: true,
    }),
  ).toHaveCount(0);
  const incomplete = await page.request.post(
    `/api/v1/organisations/${org}/exam-attempts/${state.attemptId}/score`,
    { headers: headers(admin) },
  );
  expect(incomplete.status()).toBe(200);
  const run = await incomplete.json();
  expect(run.id).toBeTruthy();
  await page
    .getByRole("button", { name: "Refresh assignments", exact: true })
    .click();
  await target
    .getByRole("link", {
      name: "Inspect score, review and release →",
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/institution\/results\/[0-9a-f-]+$/);
  const resultId = new URL(page.url()).pathname.split("/").pop();
  const versions = await page.request.get(
    `/api/v1/organisations/${org}/results/${resultId}/versions`,
    { headers: headers(admin) },
  );
  expect(versions.status()).toBe(200);
  const result = (await versions.json()).sort(
    (a, b) => b.version_number - a.version_number,
  )[0];
  expect(result.status).toBe("INCOMPLETE");
  expect(result.scoring_run_id).toBe(run.id);
  expect(result.assessment_components).toEqual([]);
  await expect(
    page.locator("p").filter({ hasText: /^Result status:/ }),
  ).toContainText("INCOMPLETE");
  await expect(
    page.getByRole("button", {
      name: "Request human result review",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Release approved result", exact: true }),
  ).toHaveCount(0);
  for (const action of ["request-review", "release"]) {
    const denied = await page.request.post(
      `/api/v1/organisations/${org}/result-versions/${result.result_version_id}/${action}`,
      {
        headers: headers(admin),
        data: action === "release" ? { operation_id: randomUUID() } : undefined,
      },
    );
    expect(denied.status()).toBe(409);
  }
  await screenshot(page, "speaking-results-unapproved-blocked.png");
  await logout(page);
  const candidate = await login(page, PENDING);
  for (const [path, data] of [
    [`/organisations/${org}/exam-attempts/${state.attemptId}/score`, undefined],
    [
      `/organisations/${org}/result-versions/${result.result_version_id}/release`,
      { operation_id: randomUUID() },
    ],
  ]) {
    const denied = await page.request.post(`/api/v1${path}`, {
      headers: headers(candidate),
      data,
    });
    expect([401, 403]).toContain(denied.status());
  }
  const candidateReleases = await page.request.get(
    "/api/v1/candidate/releases",
    { headers: headers(candidate) },
  );
  expect(candidateReleases.status()).toBe(200);
  expect(await candidateReleases.json()).toEqual([]);
  await logout(page);
  const outsider = await login(page, "speaking.outsider@demo.prufai.local");
  for (const path of [
    `/organisations/${org}/exam-attempts/${state.attemptId}/score`,
    `/organisations/${org}/result-versions/${result.result_version_id}/release`,
  ]) {
    const denied = await page.request.post(`/api/v1${path}`, {
      headers: headers(outsider),
      data: { operation_id: randomUUID() },
    });
    expect([403, 404]).toContain(denied.status());
  }
  const audio = await page.request.get(
    `/api/v1/organisations/${org}/candidate-attempts/${state.attemptId}/items/${state.bindingId}/audio-assets/${state.asset.media_asset_id}/stream`,
    { headers: headers(outsider) },
  );
  expect([403, 404]).toContain(audio.status());
  await logout(page);
});
