// The parent runs this fixture only in a private Playwright invocation.
const { test, expect } = require("@playwright/test");
const http = require("node:http");
const { enrollmentStatus, fillSecret } = require(process.env.PRUFAI_ENROLLMENT_PROBE_HELPER);

test.use({ trace: "off", screenshot: "off", video: "off" });

const secrets = JSON.parse(process.env.PRUFAI_FAILURE_LOG_SENTINELS);

async function withFault(request, kind, run) {
  let observed = false;
  const server = http.createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => { body += chunk; });
    incoming.on("end", () => {
      observed = incoming.headers.authorization === "Bearer " + secrets.access &&
        incoming.headers.cookie?.includes("prufai_refresh=" + secrets.refresh) &&
        [secrets.invitation, secrets.reset, secrets.password].every((value) => body.includes(value));
      if (kind === "drop") incoming.socket.destroy();
      if (kind === "timeout" || kind === "drop") return;
      response.writeHead(kind === "deny" ? 401 : 502, { "Content-Type": "text/plain" });
      response.end(secrets.response);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  try {
    const url = "http://127.0.0.1:" + server.address().port + "/api/v1/auth/password-reset/confirm";
    const options = {
      method: "POST",
      headers: { Authorization: "Bearer " + secrets.access },
      data: {
        invitation_token: secrets.invitation,
        token: secrets.reset,
        new_password: secrets.password,
      },
      timeout: 1500,
    };
    await request.addCookies([{
      name: "prufai_refresh",
      value: secrets.refresh,
      url,
    }]);
    await run(request.request, url, options);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    expect(Boolean(observed), "loopback server must receive the credential-bearing request").toBe(true);
  }
}

test("connection failure", async ({ context }) => {
  await withFault(context, "drop", async (request, url, options) => {
    if (process.env.PRUFAI_FAILURE_LOG_UNSAFE_CONTROL === "1") {
      await request.fetch(url, options);
      return;
    }
    await enrollmentStatus(request, "connection_probe", url, options, 401);
  });
});

test("request timeout", async ({ context }) => {
  await withFault(context, "timeout", (request, url, options) =>
    enrollmentStatus(request, "timeout_probe", url, options, 401));
});

test("unexpected HTTP status", async ({ context }) => {
  await withFault(context, "status", (request, url, options) =>
    enrollmentStatus(request, "status_probe", url, options, 401));
});

test("authorization denial", async ({ context }) => {
  await withFault(context, "deny", (request, url, options) =>
    enrollmentStatus(request, "denial_probe", url, options, 200));
});

test("reset-token request failure", async ({ context }) => {
  await withFault(context, "drop", (request, url, options) =>
    enrollmentStatus(request, "reset_probe", url, options, 400));
});

test("assertion failure with populated credential fields", async ({ page }) => {
  await page.setContent('<label>Private password <input type="password"></label>');
  await fillSecret(page.getByLabel("Private password"), secrets.form);
  expect(401, "assertion_probe unexpected HTTP status").toBe(200);
});
