const { spawnSync } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const { mkdtempSync, mkdirSync, readFileSync, readdirSync, copyFileSync, rmSync, writeFileSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const cases = [
  "connection_probe transport_unavailable",
  "timeout_probe request_timeout",
  "status_probe unexpected HTTP status",
  "denial_probe unexpected HTTP status",
  "reset_probe transport_unavailable",
  "assertion_probe unexpected HTTP status",
];

function contains(buffer, secrets) {
  return Object.values(secrets).some((secret) => buffer.includes(secret));
}

function files(directory) {
  return readdirSync(directory, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => path.join(entry.parentPath, entry.name));
}

function verify(root) {
  const cli = process.argv[2];
  if (!cli) throw new Error("playwright_cli_missing");
  const secrets = Object.fromEntries(
    ["access", "refresh", "invitation", "reset", "password", "form", "response"]
      .map((key) => [key, randomUUID()]),
  );
  const uploads = path.join(root, "uploads");
  const capture = path.join(root, "private");
  for (const directory of [uploads, capture]) mkdirSync(directory, { mode: 0o700 });
  const config = path.join(root, "playwright.config.cjs");
  writeFileSync(config, `module.exports = {
    ...require(${JSON.stringify(path.join(__dirname, "playwright.config.cjs"))}),
    testDir: __dirname, timeout: 20_000
  };\n`, { mode: 0o600 });
  copyFileSync(path.join(__dirname, "failure-log-fixture.cjs"), path.join(root, "pilot-enrollment.spec.cjs"));
  const env = {
    ...process.env,
    PRUFAI_E2E_BASE_URL: "http://127.0.0.1:1",
    PRUFAI_E2E_ALLOW_DEMO: "1",
    PRUFAI_E2E_ARTIFACT_DIR: uploads,
    PRUFAI_E2E_ENROLLMENT_CAPTURE: path.join(capture, "messages.jsonl"),
    PRUFAI_ENROLLMENT_PROBE_HELPER: path.join(__dirname, "enrollment-probes.cjs"),
    PRUFAI_FAILURE_LOG_SENTINELS: JSON.stringify(secrets),
  };
  for (const key of ["DEBUG", "DEBUG_FILE", "PWDEBUG", "NODE_DEBUG", "NODE_DEBUG_NATIVE"]) delete env[key];
  const args = [cli, "test", "--config", config, "--project", "enrollment"];
  const run = (extraEnv, ...flags) => spawnSync(process.execPath, [...args, ...flags], {
    cwd: root, env: { ...env, ...extraEnv }, timeout: 90_000, maxBuffer: 2 * 1024 * 1024,
  });

  // Run the original unsafe call as a negative control. Pipes are private, and
  // neither child output nor any secret is ever forwarded to the parent reporter.
  const unsafe = run({ PRUFAI_FAILURE_LOG_UNSAFE_CONTROL: "1" }, "--grep", "connection failure");
  if (unsafe.status !== 1 || !unsafe.stdout?.includes(secrets.access) ||
      !unsafe.stdout.includes(secrets.refresh)) {
    throw new Error("negative_control_not_detected");
  }
  const safe = run({ PRUFAI_FAILURE_LOG_UNSAFE_CONTROL: "0" });
  if (!safe.stdout || !safe.stderr || contains(safe.stdout, secrets) || contains(safe.stderr, secrets)) {
    throw new Error("secret_detected_in_reporter_output");
  }
  if (safe.status !== 1 || !safe.stdout.includes("6 failed") ||
      !cases.every((message) => safe.stdout.includes(message)) ||
      safe.stdout.includes("loopback server must receive")) {
    throw new Error("expected_failures_not_observed");
  }
  const publicFiles = files(uploads);
  // This run contains only enrollment tests. Even an opaque archive here is
  // a boundary failure, because its compressed contents could hide a sentinel.
  if (publicFiles.length !== 0) throw new Error("enrollment_output_in_upload_directory");
  const privateFiles = files(capture);
  if (privateFiles.some((file) => !/[/\\](?:error-context\.md|\.last-run\.json)$/.test(file))) {
    throw new Error("unexpected_private_artifact");
  }
  if (!privateFiles.some((file) => path.basename(file) === "error-context.md" &&
      readFileSync(file).includes(secrets.form))) {
    throw new Error("private_form_snapshot_not_observed");
  }
  return `${cases.length} expected failures, 1 unsafe control detected, ${privateFiles.length} private diagnostic files, ${publicFiles.length} upload files`;
}

let root;
try {
  root = mkdtempSync(path.join(os.tmpdir(), "prufai-failure-log-"));
  console.log("FAILURE_LOG_SECRET_CHECK = PASS (" + verify(root) + ")");
} catch {
  console.error("FAILURE_LOG_SECRET_CHECK = FAIL");
  process.exitCode = 1;
} finally {
  if (root) rmSync(root, { recursive: true, force: true });
}
