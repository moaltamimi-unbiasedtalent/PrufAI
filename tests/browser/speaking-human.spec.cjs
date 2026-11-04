const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { login, logout, apiResponse, screenshot } = require("./support.cjs");

// Chromium supplies technical synthetic microphone input only. The production
// recorder, complete-input decoder, private MinIO storage and governed services
// handle every successful audio and assessment operation without API mocking.
// The judgments below are diagnostic workflow inputs over technical tone audio.
// They do not prove German speech, B1 competence or linguistic score accuracy.
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
const CANDIDATE = "speaking.candidate@demo.prufai.local";
const PRIVATE_REASON =
  "PRIVATE SYNTHETIC HUMAN AUDIO JUDGMENT: diagnostic workflow input over technical microphone capture; not a linguistic quality or CEFR competence judgment.";
const EXAM =
  "Human-only B1 German Speaking Practice — SYNTHETIC DEMO DATA — NOT OFFICIAL MATERIAL";

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

test("original audio receives a version-pinned independently approved human Speaking assessment and safe audit", async ({
  page,
}) => {
  const prohibited = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (
      request.method() !== "GET" &&
      /\/(?:speech-transcriptions|transcriptions|scoring-runs|result-versions|releases)(?:\/|$)|\/ai\/.+\/run$|\/score$|\/speaking-reviews\/[^/]+\/propose$/.test(
        path,
      )
    )
      prohibited.push(path);
  });
  let rubricPath,
    rubric,
    candidate,
    assessor,
    attemptId,
    asset,
    assessmentId,
    versionId,
    initializedChecksum,
    proposal,
    initializeBody,
    responseRevisionId;

  await test.step("two distinct authorised humans publish the exact AUDIO rubric", async () => {
    const requester = await login(page, ADMIN);
    await page
      .getByRole("link", { name: "Writing rubrics", exact: true })
      .click();
    await page
      .getByRole("link", {
        name: "Inspect rubric human-speaking-b1-v1",
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
    const response = await requested;
    expect(response.status()).toBe(200);
    rubric = await response.json();
    await expect(
      page.getByRole("button", {
        name: "Approve reviewed rubric",
        exact: true,
      }),
    ).toHaveCount(0);
    const org = headers(requester)["X-PrufAI-Organisation-ID"];
    const self = await page.request.post(
      `/api/v1/organisations/${org}${rubricPath.replace("/institution", "")}/approve`,
      {
        headers: headers(requester),
        data: {
          operation_id: randomUUID(),
          expected_checksum: rubric.checksum,
        },
      },
    );
    expect([403, 409]).toContain(self.status());
    await logout(page);
    await login(page, REVIEWER);
    await page.goto(rubricPath);
    await page
      .getByRole("button", { name: "Approve reviewed rubric", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Publish approved rubric", exact: true })
      .waitFor({ state: "visible" });
    const published = apiResponse(page, "/publish", "POST");
    await page
      .getByRole("button", { name: "Publish approved rubric", exact: true })
      .click();
    const publishedResponse = await published;
    expect(publishedResponse.status()).toBe(200);
    rubric = await publishedResponse.json();
    expect(rubric.skill).toBe("SPEAKING");
    expect(rubric.ai_assessment_policy.mode).toBe("HUMAN_ONLY");
    expect(rubric.evidence_policy.mode).toBe("AUDIO");
    await expect(
      page.getByText("Status: PUBLISHED", { exact: true }),
    ).toBeVisible();
    await screenshot(page, "speaking-human-rubric-published.png");
    await logout(page);
  });

  await test.step("institution assigns the original practice task", async () => {
    await login(page, ADMIN);
    await page
      .getByRole("link", { name: "Assign practice assessment", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Candidate", exact: true })
      .selectOption({ label: "Human Speaking Practice Candidate" });
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

  await test.step("candidate captures, binds, reloads and submits verified original audio", async () => {
    candidate = await login(page, CANDIDATE);
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
    const finalizedResponse = await finalized;
    expect(finalizedResponse.status()).toBe(200);
    asset = await finalizedResponse.json();
    expect(asset.status).toBe("READY");
    expect(asset.duration_ms).toBeGreaterThan(0);
    expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
    const savedResponse = await attached;
    expect(savedResponse.status()).toBe(200);
    responseRevisionId = (await savedResponse.json())
      .candidate_response_revision_id;
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
    await screenshot(page, "speaking-human-candidate-submitted.png");
    await logout(page);
  });

  await test.step("Reviewer A initializes the pinned response and judges each criterion from actual playback", async () => {
    assessor = await login(page, REVIEWER);
    await page
      .getByRole("link", { name: "Speaking review", exact: true })
      .click();
    const sources = page.getByRole("combobox", {
      name: "Submitted Speaking response",
      exact: true,
    });
    await sources.selectOption(responseRevisionId);
    const rubrics = page.getByRole("combobox", {
      name: "Published Speaking rubric",
      exact: true,
    });
    const selected = rubrics
      .locator("option")
      .filter({ hasText: "human-speaking-b1-v1" });
    await rubrics.selectOption(await selected.getAttribute("value"));
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
    initializeBody = response.request().postDataJSON();
    const created = await response.json();
    assessmentId = created.id;
    versionId = created.version_id;
    initializedChecksum = created.checksum;
    await expect(
      page.locator("p").filter({ hasText: /^Assessment origin:/ }),
    ).toContainText("HUMAN");
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
        .fill(String(Math.min(asset.duration_ms, 1000)));
      await page
        .getByRole("textbox", {
          name: `Private human rationale for ${code}`,
          exact: true,
        })
        .fill(PRIVATE_REASON);
    }
    const proposed = apiResponse(page, "/human-proposal", "POST");
    await page
      .getByRole("button", {
        name: "Submit human Speaking assessment for independent review",
        exact: true,
      })
      .click();
    const proposalResponse = await proposed;
    expect(proposalResponse.status()).toBe(200);
    proposal = await proposalResponse.json();
    expect(proposal.status).toBe("IN_REVIEW");
    const org = headers(assessor)["X-PrufAI-Organisation-ID"];
    const self = await page.request.post(
      `/api/v1/organisations/${org}/speaking-reviews/${versionId}/human-approve`,
      {
        headers: headers(assessor),
        data: {
          operation_id: randomUUID(),
          expected_checksum: proposal.checksum,
        },
      },
    );
    expect([403, 409]).toContain(self.status());
    await expect(
      page.getByRole("button", {
        name: "Approve human Speaking assessment",
        exact: true,
      }),
    ).toHaveCount(0);
    await expect(
      page.getByText(/Your judgments await an independent reviewer/),
    ).toBeVisible();
    await screenshot(page, "speaking-human-awaiting-independent-review.png");
    await logout(page);
  });

  await test.step("a distinct Reviewer B inspects original audio and approves the exact human version", async () => {
    const independent = await login(page, ADMIN);
    await page.goto(`/institution/speaking-reviews/${assessmentId}`);
    await expect(
      page.getByRole("button", {
        name: "Approve human Speaking assessment",
        exact: true,
      }),
    ).toBeVisible();
    await playOriginal(page);
    const org = headers(independent)["X-PrufAI-Organisation-ID"];
    const stale = await page.request.post(
      `/api/v1/organisations/${org}/speaking-reviews/${versionId}/human-approve`,
      {
        headers: headers(independent),
        data: {
          operation_id: randomUUID(),
          expected_checksum: initializedChecksum,
        },
      },
    );
    expect(stale.status()).toBe(409);
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
    await page.reload();
    await expect(
      page.locator("p").filter({ hasText: /^Assessment status:/ }),
    ).toContainText("APPROVED");
    await expect(
      page.getByRole("button", {
        name: "Submit human Speaking assessment for independent review",
        exact: true,
      }),
    ).toHaveCount(0);
    await page
      .getByText("Immutable assessment version history", { exact: true })
      .click();
    await expect(
      page.getByText(versionId, { exact: true }).first(),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await screenshot(page, "speaking-human-approved-mobile.png");
    await page.setViewportSize({ width: 1280, height: 900 });
    await logout(page);
  });

  await test.step("candidate and wrong-tenant institution have no review or audio authority", async () => {
    const actor = await login(page, CANDIDATE);
    await page.goto(`/institution/speaking-reviews/${assessmentId}`);
    await expect(
      page.getByRole("heading", { name: "Access restricted", exact: true }),
    ).toBeVisible();
    const org = headers(actor)["X-PrufAI-Organisation-ID"];
    const candidateDenied = await page.request.post(
      `/api/v1/organisations/${org}/speaking-reviews/${versionId}/human-approve`,
      {
        headers: headers(actor),
        data: {
          operation_id: randomUUID(),
          expected_checksum: proposal.checksum,
        },
      },
    );
    expect([401, 403]).toContain(candidateDenied.status());
    await logout(page);
    const outsider = await login(page, "speaking.outsider@demo.prufai.local");
    const outsideOrg = headers(outsider)["X-PrufAI-Organisation-ID"];
    for (const path of [
      `/organisations/${outsideOrg}/speaking-reviews/${assessmentId}`,
      `/organisations/${org}/speaking-reviews/${assessmentId}`,
      `/organisations/${outsideOrg}/audio-evidence/${asset.media_asset_id}`,
      `/organisations/${org}/audio-evidence/${asset.media_asset_id}`,
      `/candidate/audio-assets/${asset.media_asset_id}/stream`,
    ]) {
      const denied = await page.request.get(`/api/v1${path}`, {
        headers: headers(outsider),
      });
      expect([403, 404]).toContain(denied.status());
      expect(await denied.text()).not.toContain(PRIVATE_REASON);
    }
    await page.goto(`/institution/speaking-reviews/${assessmentId}`);
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.locator("audio")).toHaveCount(0);
    await logout(page);
  });

  await test.step("Control Centre exposes safe governance references without private rationale or media", async () => {
    const platform = await login(page, "platform.admin@demo.prufai.local");
    const org = headers(candidate)["X-PrufAI-Organisation-ID"];
    const suppliedOrg = {
      Authorization: `Bearer ${platform.token}`,
      "X-PrufAI-Organisation-ID": org,
    };
    for (const path of [
      `/organisations/${org}/speaking-reviews/${assessmentId}`,
      `/organisations/${org}/speaking-review-options`,
      `/organisations/${org}/audio-evidence/${asset.media_asset_id}`,
      `/organisations/${org}/candidate-attempts/${attemptId}/items/${initializeBody.exam_version_item_binding_id}/audio-assets/${asset.media_asset_id}/stream`,
    ]) {
      const denied = await page.request.get(`/api/v1${path}`, {
        headers: suppliedOrg,
      });
      expect(denied.status()).toBe(403);
    }
    const deniedInit = await page.request.post(
      `/api/v1/organisations/${org}/subjective-assessments/human-speaking`,
      {
        headers: suppliedOrg,
        data: { ...initializeBody, operation_id: randomUUID() },
      },
    );
    expect(deniedInit.status()).toBe(403);
    await page
      .getByRole("link", { name: "Audit evidence", exact: true })
      .click();
    for (const [event, entityId] of [
      ["rubric.published", rubric.id],
      ["subjective_assessment.human_initialized", versionId],
      ["subjective_assessment.human_proposed", versionId],
      ["subjective_assessment.human_approved", versionId],
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
      expect(response.ok()).toBe(true);
      expect(JSON.stringify(await response.json())).not.toMatch(
        /PRIVATE SYNTHETIC HUMAN AUDIO JUDGMENT|storage_key|audio_bytes|private_url/,
      );
      await expect(
        page
          .getByRole("row")
          .filter({ hasText: event })
          .getByRole("cell", { name: entityId, exact: true }),
      ).toBeVisible();
    }
    await screenshot(page, "speaking-human-platform-safe-audit.png");
    await logout(page);
  });
  expect(prohibited).toEqual([]);
});
