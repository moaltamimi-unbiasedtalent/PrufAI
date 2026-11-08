const { readFileSync } = require("node:fs");
const { randomUUID } = require("node:crypto");
const { expect, test } = require("@playwright/test");
const { apiResponse, login, logout } = require("./support.cjs");

test("verified TEST ONLY privacy reviewer discovers persisted evidence and receives no erasure authority", async ({
  page,
  browser,
}) => {
  const fixture = JSON.parse(
    readFileSync(process.env.PRUFAI_E2E_PRIVACY_MANIFEST, "utf8"),
  );
  const path = `/api/v1/privacy/organisations/${fixture.organisation}/cases`;
  const headers = (actor) => ({
    Authorization: `Bearer ${actor.token}`,
    "X-PrufAI-Organisation-ID": fixture.organisation,
  });
  const admin = await login(page, "admin@demo.prufai.local");
  const intake = await page.request.post(path, {
    headers: headers(admin),
    data: {
      operation_id: randomUUID(),
      request_receipt: randomUUID(),
      subject_id: fixture.subject,
      request_type: "ERASURE_REQUEST",
      purpose: "ASSESSMENT_DELIVERY",
      categories: [
        "ASSIGNMENTS",
        "ATTEMPTS",
        "OBJECTIVE_ANSWERS",
        "SCORING_RESULTS",
        "DATABASE_BACKUPS",
      ],
    },
  });
  expect(intake.status()).toBe(200);
  const row = await intake.json();
  expect(row.identity).toBe("VERIFICATION_REQUIRED");
  const casePath = `${path}/${row.id}`;
  const reviewerPage = await browser.newPage();
  try {
    const reviewer = await login(reviewerPage, fixture.reviewer);
    const scope = await reviewerPage.request.post(casePath + "/verify", {
      headers: headers(reviewer),
      data: {
        operation_id: randomUUID(),
        case_checksum: row.case_checksum,
        verification_receipt: randomUUID(),
        controller_context: "UNASSESSED",
      },
    });
    expect(scope.status()).toBe(200);
    const verified = await scope.json();
    const hold = await reviewerPage.request.post(casePath + "/hold-reviews", {
      headers: headers(reviewer),
      data: {
        operation_id: randomUUID(),
        case_checksum: verified.case_checksum,
        status: "REVIEW_REQUIRED",
        basis: "UNASSESSED",
        decision_receipt: randomUUID(),
        starts_at: new Date(Date.now() - 60_000).toISOString(),
        review_due_at: new Date(Date.now() + 3_600_000).toISOString(),
      },
    });
    expect(hold.status()).toBe(200);
    await reviewerPage.goto("/privacy");
    await reviewerPage
      .getByRole("button", { name: /Open erasure request case/ })
      .click();
    await expect(
      reviewerPage.getByText("verified test only", { exact: true }),
    ).toBeVisible();
    await expect(
      reviewerPage.getByText("UNCONFIGURED", { exact: true }),
    ).toBeVisible();
    const result = apiResponse(reviewerPage, "/preview", "POST");
    await reviewerPage
      .getByRole("button", { name: "Run read-only preview" })
      .click();
    const response = await result;
    expect(response.status()).toBe(200);
    expect(response.headers()["cache-control"]).toContain("no-store");
    const body = await response.json();
    expect(body.eligibility).toBe("NOT_DETERMINED");
    expect(body.physical_erasure_authorized).toBe(false);
    expect(
      body.categories.find((item) => item.category === "OBJECTIVE_ANSWERS")
        .count,
    ).toBeGreaterThan(0);
    await expect(
      reviewerPage.getByRole("status").filter({ hasText: "NOT_DETERMINED" }),
    ).toBeVisible();
    await expect(
      reviewerPage.getByText(/objective answers.*[1-9] records/),
    ).toBeVisible();
    await expect(
      reviewerPage.getByText(
        /database backups.*count unavailable.*unsupported/i,
      ),
    ).toBeVisible();
    await expect(
      reviewerPage.getByText(/Hold assessment.*review required/),
    ).toBeVisible();
    await expect(
      reviewerPage.getByRole("button", { name: /Erase now/i }),
    ).toHaveCount(0);

    const denied = await page.request.post(casePath + "/preview", {
      headers: headers(admin),
      data: {
        operation_id: randomUUID(),
        case_checksum: verified.case_checksum,
        scope_checksum: verified.scope_checksum,
        policy_pins: {},
      },
    });
    expect(denied.status()).toBe(403);
    await logout(page);
    const candidate = await login(page, "candidate@demo.prufai.local");
    const otherSubject = await page.request.get(casePath, {
      headers: headers(candidate),
    });
    expect(otherSubject.status()).toBe(403);
    await page.goto("/privacy");
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "Access denied or unconfigured" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run read-only preview" }),
    ).toHaveCount(0);
  } finally {
    await reviewerPage.close();
  }
});
