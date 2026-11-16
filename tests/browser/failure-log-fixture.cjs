// The parent runs this fixture only in a private Playwright invocation.
const { test, expect } = require("@playwright/test");
const http = require("node:http");
const { enrollmentStatus, fillSecret } = require(process.env.PRUFAI_ENROLLMENT_PROBE_HELPER);

test.use({ trace: "off", screenshot: "off", video: "off" });

const secrets = JSON.parse(process.env.PRUFAI_FAILURE_LOG_SENTINELS);

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
}

function close(server) {
  server.closeAllConnections();
  return new Promise((resolve) => server.close(resolve));
}

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
  await listen(server);
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
    await close(server);
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

for (const status of [301, 302, 303, 307, 308]) {
  test(`redirect guard ${status}`, async ({ context }) => {
    const me = "/api/v1/auth/me";
    const reset = "/api/v1/auth/password-reset/confirm";
    const observed = [];
    let redirectedRequests = 0;
    let redirect = true;
    const rejectionStatus = (path) => (path === me ? 401 : 400);
    const destination = http.createServer((request, response) => {
      redirectedRequests += 1;
      request.resume();
      response.writeHead(rejectionStatus(request.url)).end();
    });
    await listen(destination);
    const source = http.createServer((request, response) => {
      let body = "";
      request.on("data", (chunk) => { body += chunk; });
      request.on("end", () => {
        const refresh = request.headers.cookie?.includes("prufai_refresh=" + secrets.refresh);
        const expectedCredentials = request.url === me
          ? request.method === "GET" && request.headers.authorization === "Bearer " + secrets.access
          : request.url === reset && request.method === "POST" &&
            body.includes(secrets.reset) && body.includes(secrets.password);
        observed.push(Boolean(refresh && expectedCredentials));
        if (redirect) {
          response.writeHead(status, {
            Location: "http://127.0.0.1:" + destination.address().port + request.url,
          }).end();
          return;
        }
        response.writeHead(rejectionStatus(request.url)).end();
      });
    });
    try {
      await listen(source);
      const origin = "http://127.0.0.1:" + source.address().port;
      await context.addCookies([{
        name: "prufai_refresh", value: secrets.refresh, url: origin + "/api/v1/auth/",
      }]);
      const probes = [
        ["session_redirect_probe", me, { headers: { Authorization: "Bearer " + secrets.access } }, 401],
        ["reset_redirect_probe", reset, {
          method: "POST", data: { token: secrets.reset, new_password: secrets.password },
        }, 400],
      ];
      const run = ([label, path, options, expected]) => enrollmentStatus(
        context.request, label, origin + path, { ...options, maxRedirects: 2, timeout: 1500 }, expected,
      );
      const failures = [];
      for (const probe of probes) {
        try {
          await run(probe);
          failures.push("unexpected_success");
        } catch (error) {
          const message = probe[0] + " unexpected_redirect (" + status + ")";
          failures.push(
            error.message === message && error.cause === undefined &&
              !Object.values(secrets).some((secret) => error.stack?.includes(secret))
              ? "safe_redirect_failure"
              : "unexpected_failure",
          );
        }
      }
      expect(redirectedRequests, "second origin must receive no request").toBe(0);
      expect(failures).toEqual(["safe_redirect_failure", "safe_redirect_failure"]);
      redirect = false;
      for (const probe of probes) await run(probe);
      expect(observed, "intended origin must receive both credential-bearing probes in each mode")
        .toEqual([true, true, true, true]);
      expect(redirectedRequests).toBe(0);
    } finally {
      await Promise.all([close(source), close(destination)]);
    }
  });
}
