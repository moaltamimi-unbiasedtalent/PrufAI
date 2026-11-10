const { test, expect } = require("@playwright/test");
const { existsSync, rmSync, writeFileSync } = require("node:fs");
const { join } = require("node:path");
const { apiResponse, login } = require("./support.cjs");

test("frontend reports a real API outage and signs in after the owned API restarts", async ({
  page,
}) => {
  const directory = process.env.PRUFAI_E2E_API_RESTART_DIR;
  if (!directory) throw new Error("The guarded browser runner is required.");
  const marker = (name) => join(directory, name);
  const touch = (name) =>
    writeFileSync(marker(name), "", { flag: "wx", mode: 0o600 });
  const email = "candidate@demo.prufai.local";
  const password = process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!";

  await page.goto("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);

  rmSync(marker("complete"), { force: true });
  touch("hold");
  touch("request");
  try {
    await expect.poll(() => existsSync(marker("paused"))).toBe(true);
    const failure = apiResponse(page, "/api/v1/auth/login", "POST");
    await page.getByRole("button", { name: "Sign in to workspace" }).click();
    expect((await failure).status()).toBeGreaterThanOrEqual(500);
    await expect(page.getByRole("alert")).toHaveText(
      "Sign in is unavailable. Check that the API and database are running.",
    );
    await expect(page).toHaveURL(/\/login$/);
  } finally {
    if (existsSync(marker("paused"))) touch("resume");
    await expect
      .poll(() => existsSync(marker("complete")), { timeout: 45_000 })
      .toBe(true);
  }

  await login(page, email);
});
