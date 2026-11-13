const { expect } = require("@playwright/test");
const { existsSync, readFileSync, rmSync, writeFileSync } = require("node:fs");
const path = require("node:path");
const { fillSecret, readSignInToken } = require("./enrollment-probes.cjs");
const password = process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!";

async function login(page, email) {
  await page.goto("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await fillSecret(page.getByLabel("Password", { exact: true }), password);
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith("/api/v1/auth/login") &&
      item.request().method() === "POST",
  );
  const identity = page.waitForResponse(
    (item) => item.url().endsWith("/api/v1/auth/me") && item.status() === 200,
  );
  await page
    .getByRole("button", { name: "Sign in to workspace", exact: true })
    .click();
  expect((await response).status()).toBe(200);
  const token = await readSignInToken(await response);
  const profile = await (await identity).json();
  await expect(page).not.toHaveURL(/\/login$/);
  return { token, profile };
}

async function logout(page) {
  const response = page.waitForResponse(
    (item) =>
      item.url().endsWith("/api/v1/auth/logout") &&
      item.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  expect((await response).status()).toBe(204);
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
}

function apiResponse(page, suffix, method = "GET") {
  return page.waitForResponse(
    (item) =>
      new URL(item.url()).pathname.endsWith(suffix) &&
      item.request().method() === method,
  );
}

async function screenshot(page, name) {
  await page.screenshot({
    path: path.join(process.env.PRUFAI_E2E_ARTIFACT_DIR, name),
    fullPage: true,
  });
}

async function restartOwnedApi() {
  const directory = process.env.PRUFAI_E2E_API_RESTART_DIR;
  if (!directory)
    throw new Error(
      "The guarded browser runner restart handshake is required.",
    );
  const completion = path.join(directory, "complete");
  rmSync(completion, { force: true });
  writeFileSync(path.join(directory, "request"), "restart owned API\n", {
    flag: "wx",
    mode: 0o600,
  });
  await expect
    .poll(() => existsSync(completion), { timeout: 45_000 })
    .toBe(true);
  const [oldPid, newPid] = readFileSync(completion, "utf8").trim().split(" ");
  expect(oldPid).toMatch(/^\d+$/);
  expect(newPid).toMatch(/^\d+$/);
  expect(newPid).not.toBe(oldPid);
}

module.exports = { login, logout, apiResponse, screenshot, restartOwnedApi };
