const { expect } = require("@playwright/test");
const path = require("node:path");
const password = process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!";

async function login(page, email) {
  await page.goto("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
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
  const token = (await (await response).json()).access_token;
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

module.exports = { login, logout, apiResponse, screenshot };
