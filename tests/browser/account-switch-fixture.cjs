const { expect } = require("@playwright/test");
const { randomUUID } = require("node:crypto");

// Called only by the private enrollment project: no traces, DOM uploads or
// value-bearing assertions. These restored sessions are synthetic, not API proof.
async function accountSwitchFailures(browser, baseURL, german) {
  const unconfirmed = german
    ? "Die Abmeldung konnte nicht bestätigt werden. Sie bleiben angemeldet. Bitte versuchen Sie die Abmeldung erneut, bevor Sie das Konto wechseln."
    : "Sign-out could not be confirmed. You remain signed in. Please retry signing out before switching accounts.";
  const cleanupWarning = german
    ? "Sie sind auf dem Server abgemeldet. Offline-Daten konnten nicht gelöscht werden. Schließen Sie diesen Tab, bevor Sie das Gerät weitergeben."
    : "Signed out on the server. Offline data could not be cleared. Close this tab before sharing this device.";

  for (const mode of [
    "transport",
    "getter",
    "http503",
    "csrf_missing",
    "csrf_failed",
    "cleanup_failed",
    "held",
  ]) {
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    const canary = "synthetic-private-signout-" + randomUUID();
    const organisationId = randomUUID();
    const profile = {
      id: randomUUID(),
      email: "synthetic-switch@example.test",
      display_name: null,
      memberships: [
        {
          organisation_id: organisationId,
          organisation_name: "Synthetic institution",
          status: "ACTIVE",
          role_keys: ["EXAMINER"],
          permissions: [],
        },
      ],
      platform_scope: false,
      platform_permissions: [],
      v1_pilot_profile: "DISABLED",
    };
    const database = `prufai-candidate-runtime-v2:${profile.id}:${organisationId}`;
    let armed = false;
    let allow = false;
    let logoutRequests = 0;
    let unexpectedRequests = 0;
    let consoleCanary = false;
    let release;
    let observed;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    const requested = new Promise((resolve) => {
      observed = resolve;
    });
    page.on("console", (message) => {
      consoleCanary ||= message.text().includes(canary);
    });
    page.on("pageerror", (error) => {
      consoleCanary ||= String(error).includes(canary);
    });
    try {
      await page.route("**/api/**", async (route) => {
        const path = new URL(route.request().url()).pathname;
        let body;
        if (path === "/api/v1/auth/csrf") {
          if (armed && !allow && mode === "csrf_missing")
            return route.fulfill({
              status: 200,
              contentType: "application/json",
              body: "{}",
            });
          body = { csrf_token: "synthetic-csrf" };
        } else if (path === "/api/v1/auth/refresh") {
          body = { access_token: "synthetic-bearer" };
        } else if (path === "/api/v1/auth/me") body = profile;
        else if (path === "/api/v1/auth/logout") {
          logoutRequests += 1;
          observed();
          if (mode === "held" && !allow) await held;
          return route.fulfill({
            status:
              allow || mode === "cleanup_failed" || mode === "held" ? 204 : 503,
          });
        } else {
          unexpectedRequests += 1;
          return route.fulfill({ status: 403, body: "{}" });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(body),
        });
      });
      await page.goto(german ? "/anmelden" : "/login");
      const button = page.getByRole("button", {
        name: german ? "Abmelden" : "Sign out to switch account",
        exact: true,
      });
      const email = page.getByLabel(german ? "E-Mail-Adresse" : "Email", {
        exact: true,
      });
      const password = page.getByLabel(german ? "Passwort" : "Password", {
        exact: true,
      });
      const alert = page.locator("form").getByRole("alert");
      await expect(button).toBeEnabled();
      await expect(email).toBeDisabled();
      await expect(password).toBeDisabled();
      await page.evaluate(
        async ({ database, mode, canary }) => {
          // Preserve a different account's database; only the current one may clear.
          for (const name of [database, database + ":other-account"]) {
            await new Promise((resolve, reject) => {
              const request = indexedDB.open(name, 5);
              request.onupgradeneeded = () =>
                request.result.createObjectStore("response-outbox");
              request.onerror = () =>
                reject(new Error("Synthetic setup unavailable"));
              request.onsuccess = () => {
                const db = request.result;
                const tx = db.transaction("response-outbox", "readwrite");
                tx.objectStore("response-outbox").put(
                  { operationId: "unchanged", expectedRevision: 1 },
                  "queued",
                );
                tx.oncomplete = () => {
                  db.close();
                  resolve();
                };
                tx.onerror = () => {
                  db.close();
                  reject(new Error("Synthetic setup unavailable"));
                };
              };
            });
          }
          window.__signOutProbe = { getterReads: 0, failures: 0, allow: false };
          const originalFetch = window.fetch;
          window.fetch = (input, options) => {
            const path = new URL(String(input), location.origin).pathname;
            const failure =
              !window.__signOutProbe.allow &&
              ((path.endsWith("/auth/logout") &&
                ["transport", "getter"].includes(mode)) ||
                (path.endsWith("/auth/csrf") && mode === "csrf_failed"));
            if (failure) {
              window.__signOutProbe.failures += 1;
              const error =
                mode === "getter"
                  ? Object.defineProperty(new Error(), "message", {
                      get() {
                        window.__signOutProbe.getterReads += 1;
                        return canary;
                      },
                    })
                  : new TypeError(canary);
              return Promise.reject(error);
            }
            return originalFetch(input, options);
          };
          const originalDelete = indexedDB.deleteDatabase.bind(indexedDB);
          indexedDB.deleteDatabase = (name) => {
            if (mode === "cleanup_failed" && name === database)
              throw new Error(canary);
            return originalDelete(name);
          };
        },
        { database, mode, canary },
      );
      armed = true;
      await button.click();
      if (mode === "held") {
        await requested;
        await expect(button).toBeDisabled();
        await button.evaluate((element) => {
          element.click();
          element.click();
        });
        expect(logoutRequests).toBe(1);
        await expect(email).toBeDisabled();
        release();
        await expect(email).toBeEnabled();
        expect(logoutRequests).toBe(1);
        await expect(alert).toHaveCount(0);
      } else if (mode === "cleanup_failed") {
        await expect(email).toBeEnabled();
        await expect
          .poll(async () => (await alert.textContent()) === cleanupWarning)
          .toBe(true);
        await expect(alert).toBeFocused();
        await expect(alert).toHaveAttribute("lang", german ? "de" : "en");
        expect(logoutRequests).toBe(1);
        await expect(button).toHaveCount(0);
      } else {
        await expect
          .poll(async () => (await alert.textContent()) === unconfirmed)
          .toBe(true);
        await expect(alert).toBeFocused();
        await expect(alert).toHaveAttribute("lang", german ? "de" : "en");
        await expect(button).toBeEnabled();
        await expect(email).toBeDisabled();
        await expect(password).toBeDisabled();
        const expectedRequests = mode === "http503" ? 1 : 0;
        expect(logoutRequests).toBe(expectedRequests);
        expect(
          await page.evaluate(() => window.__signOutProbe.getterReads),
        ).toBe(0);
        expect(
          await page.evaluate(
            async (name) =>
              (await indexedDB.databases()).some((db) => db.name === name),
            database,
          ),
        ).toBe(true);
        allow = true;
        await page.evaluate(() => {
          window.__signOutProbe.allow = true;
        });
        await button.click();
        await expect(email).toBeEnabled();
        await expect(password).toBeEnabled();
        await expect(alert).toHaveCount(0);
        expect(logoutRequests).toBe(expectedRequests + 1);
      }
      const diagnostics = await page.evaluate(
        (canary) => ({
          canaryVisible: document.body.textContent.includes(canary),
          getterReads: window.__signOutProbe.getterReads,
        }),
        canary,
      );
      expect(diagnostics.canaryVisible).toBe(false);
      expect(diagnostics.getterReads).toBe(0);
      expect(consoleCanary).toBe(false);
      expect(unexpectedRequests).toBe(0);
      const databases = await page.evaluate(() => indexedDB.databases());
      expect(databases.some((db) => db.name === database)).toBe(
        mode === "cleanup_failed",
      );
      expect(
        databases.some((db) => db.name === database + ":other-account"),
      ).toBe(true);
    } finally {
      release();
      await context.close();
    }
  }
}

module.exports = { accountSwitchFailures };
