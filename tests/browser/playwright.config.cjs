const path = require("node:path");
const { defineConfig } = require("@playwright/test");

const baseURL = process.env.PRUFAI_E2E_BASE_URL;
if (!baseURL || process.env.PRUFAI_E2E_ALLOW_DEMO !== "1") {
  throw new Error(
    "Set PRUFAI_E2E_BASE_URL and PRUFAI_E2E_ALLOW_DEMO=1 for an isolated demo database.",
  );
}
const target = new URL(baseURL);
if (
  target.protocol !== "http:" ||
  !["127.0.0.1", "localhost"].includes(target.hostname)
) {
  throw new Error(
    "Integrated demo acceptance only targets a local HTTP application.",
  );
}

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: "*.spec.cjs",
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  outputDir: path.join(
    process.env.PRUFAI_E2E_ARTIFACT_DIR || "/tmp/prufai-integrated-browser",
    "results",
  ),
  use: {
    baseURL,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    viewport: { width: 1280, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
