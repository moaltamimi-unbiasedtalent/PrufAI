const { test, expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");
const { readFileSync } = require("node:fs");
const { enrollmentStatus, fillSecret, readSignInToken } = require("./enrollment-probes.cjs");
const { apiResponse, login, logout, restartOwnedApi } = require("./support.cjs");

// One-time codes and passwords must stay out of failure artifacts and traces.
test.use({ trace: "off", screenshot: "off", video: "off" });

async function captureFor(recipient, purpose) {
  const path = process.env.PRUFAI_E2E_ENROLLMENT_CAPTURE;
  if (!path) throw new Error("The disposable enrollment capture is required.");
  let message;
  await expect.poll(() => {
    let data;
    try {
      data = readFileSync(path, "utf8");
    } catch (error) {
      if (error.code === "ENOENT") return false;
      throw error;
    }
    // A single write per message keeps this file readable under concurrent requests.
    try {
      message = data.trim().split("\n").filter(Boolean).map(JSON.parse).find(
        (entry) => entry.recipient === recipient && entry.purpose === purpose,
      );
    } catch {
      throw new Error("The private enrollment capture could not be read.");
    }
    return Boolean(message);
  }).toBe(true);
  return message;
}

async function germanSignIn(page, email, password, expectedStatus) {
  await page.goto("/anmelden");
  await page.getByLabel("E-Mail-Adresse", { exact: true }).fill(email);
  await fillSecret(page.getByLabel("Passwort", { exact: true }), password);
  const response = apiResponse(page, "/api/v1/auth/login", "POST");
  await page.getByRole("button", { name: "Anmelden", exact: true }).click();
  const result = await response;
  expect(result.status()).toBe(expectedStatus);
  if (expectedStatus !== 200) return;
  const token = await readSignInToken(result);
  await expect(page).toHaveURL(/\/candidate$/);
  return token;
}

async function accept(page, invite, organisation, password, expectedStatus) {
  await page.goto("/einladung");
  const institution = page.getByLabel("Einrichtungs-ID");
  const code = page.getByLabel("Einladungscode");
  await institution.fill(organisation);
  await institution.focus();
  await page.keyboard.press("Tab");
  await expect(code).toBeFocused();
  await fillSecret(code, invite.token);
  await fillSecret(page.getByLabel("Neues Passwort", { exact: true }), password);
  await fillSecret(page.getByLabel("Passwort wiederholen"), password);
  const response = apiResponse(page, "/invitations/accept", "POST");
  await page.getByRole("button", { name: "Konto erstellen" }).click();
  expect((await response).status()).toBe(expectedStatus);
}

test("institution invitation and candidate recovery use real disposable authentication", async ({ page, browser }) => {
  const email = "enrollment-" + randomUUID() + "@example.test";
  const originalPassword = "Synthetic enrollment phrase!";
  const newPassword = "Synthetic replacement phrase!";
  const { profile } = await login(page, "admin@demo.prufai.local");
  const org = profile.memberships.find(({ status }) => status === "ACTIVE").organisation_id;
  await page.getByRole("link", { name: "Person zur Übung einladen" }).click();
  await page.getByLabel("E-Mail-Adresse").fill(email);
  const created = apiResponse(page, "/invitations", "POST");
  await page.getByRole("button", { name: "Einladung erstellen" }).click();
  const receipt = await created;
  expect(receipt.status()).toBe(200);
  const invite = await captureFor(email, "invitation");
  expect(invite.organisation_id).toBe(org);
  expect((await receipt.text()).includes(invite.token)).toBe(false);
  await logout(page);
  await restartOwnedApi();

  await page.setViewportSize({ width: 390, height: 844 });
  await accept(page, invite, randomUUID(), originalPassword, 400);
  const alert = page.locator("main").getByRole("alert");
  await expect(alert).toHaveText("Diese Einladung gehört zu einer anderen Einrichtung.");
  await expect(alert).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await accept(page, invite, org, originalPassword, 200);
  await expect(page.getByText("Ihr Konto wurde erstellt.", { exact: false })).toBeVisible();
  await accept(page, invite, org, originalPassword, 400);
  await expect(alert).toContainText("bereits verwendet");

  const signedOut = await germanSignIn(page, email, originalPassword, 200);
  const logoutResponse = apiResponse(page, "/api/v1/auth/logout", "POST");
  await page.getByRole("button", { name: "Abmelden", exact: true }).click();
  expect((await logoutResponse).status()).toBe(204);
  await expect(page).toHaveURL(/\/anmelden$/);
  const me = "/api/v1/auth/me";
  const bearer = (token) => ({ Authorization: "Bearer " + token });
  await enrollmentStatus(page.request, "signed_out_session_check", me, {
    headers: bearer(signedOut),
  }, 401);

  const other = await browser.newContext();
  try {
    const active = await germanSignIn(await other.newPage(), email, originalPassword, 200);
    await page.getByRole("link", { name: "Passwort vergessen" }).click();
    await expect(page).toHaveURL(/\/konto\/wiederherstellen$/);
    await expect(page.getByRole("heading", { name: "Kontozugang wiederherstellen" })).toBeVisible();
    await page.getByLabel("E-Mail-Adresse").fill(email);
    const requested = apiResponse(page, "/password-reset/request", "POST");
    await page.getByRole("button", { name: "Wiederherstellung anfragen" }).click();
    const reply = await requested;
    expect(reply.status()).toBe(202);
    await expect(page.getByText("Eine Zustellung wird hier nicht bestätigt.", { exact: false })).toBeVisible();
    const reset = await captureFor(email, "password_reset");
    expect((await reply.text()).includes(reset.token)).toBe(false);
    await page.getByRole("link", { name: "Wiederherstellungscode eingeben" }).click();
    await fillSecret(page.getByLabel("Wiederherstellungscode"), reset.token);
    await fillSecret(page.getByLabel("Neues Passwort", { exact: true }), newPassword);
    await fillSecret(page.getByLabel("Passwort wiederholen"), newPassword);
    const confirmed = apiResponse(page, "/password-reset/confirm", "POST");
    await page.getByRole("button", { name: "Passwort ändern" }).click();
    expect((await confirmed).status()).toBe(200);
    await expect(page.getByText("Ihr Passwort wurde geändert.", { exact: false })).toBeVisible();
    await enrollmentStatus(other.request, "recovered_session_check", me, {
      headers: bearer(active),
    }, 401);
    await enrollmentStatus(page.request, "reset_replay_check", "/api/v1/auth/password-reset/confirm", {
      method: "POST",
      data: { token: reset.token, new_password: newPassword },
    }, 400);
    await germanSignIn(page, email, originalPassword, 401);
    await expect(alert).toContainText("Anmeldung ist fehlgeschlagen");
    await germanSignIn(page, email, newPassword, 200);
  } finally {
    await other.close();
  }
});
