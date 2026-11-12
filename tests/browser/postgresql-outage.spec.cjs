const { test, expect } = require("@playwright/test");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { apiResponse, login } = require("./support.cjs");

const postgres = (action) =>
  promisify(execFile)("python3", ["scripts/browser-postgres-outage.py", action]);
const email = "candidate@demo.prufai.local";
const password = process.env.PRUFAI_DEMO_PASSWORD || "Demo-PrufAI-2026!";
const api = process.env.PRUFAI_API_BASE_URL;

test("real PostgreSQL outage keeps liveness and permits an explicit sign-in retry after recovery", async ({
  page,
  browser,
  request,
}) => {
  test.setTimeout(90_000);
  expect((await request.get(`${api}/ready`)).status()).toBe(200);
  const healthy = await browser.newContext();
  try {
    await login(await healthy.newPage(), email);
  } finally {
    await healthy.close();
  }

  await page.goto("/login");
  await expect(page.getByLabel("Email", { exact: true })).toBeEnabled();
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  try {
    await postgres("stop");
    expect((await request.get(`${api}/health`)).status()).toBe(200);
    const ready = await request.get(`${api}/ready`);
    expect(ready.status()).toBe(503);
    expect(await ready.json()).toEqual({ status: "unavailable" });

    const failed = apiResponse(page, "/api/v1/auth/login", "POST");
    await page.getByRole("button", { name: "Sign in to workspace" }).click();
    const failure = await failed;
    expect(failure.status()).toBe(503);
    expect(await failure.json()).toEqual({
      detail: { code: "AUTH_DATABASE_UNAVAILABLE" },
    });
    expect((await failure.headersArray()).filter(({ name }) =>
      name.toLowerCase() === "set-cookie",
    )).toEqual([]);
    const alert = page.locator("form").getByRole("alert");
    await expect(alert).toHaveText(
      "Die Anmeldung ist vorübergehend nicht verfügbar. Bitte versuchen Sie es erneut.",
    );
    await expect(alert).toHaveAttribute("lang", "de");
    await expect(alert).toBeFocused();
    await expect(page.getByLabel("Email", { exact: true })).toHaveValue(email);
    await expect(page).toHaveURL(/\/login$/);
  } finally {
    await postgres("start");
  }

  await expect.poll(async () => (await request.get(`${api}/ready`)).status(), {
    timeout: 30_000,
  }).toBe(200);
  const recovered = apiResponse(page, "/api/v1/auth/login", "POST");
  await page.getByRole("button", { name: "Sign in to workspace" }).click();
  expect((await recovered).status()).toBe(200);
  await expect(page).not.toHaveURL(/\/login$/);
  await expect(page.locator("form").getByRole("alert")).toHaveCount(0);
});
