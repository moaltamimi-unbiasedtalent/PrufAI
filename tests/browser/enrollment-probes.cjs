const { expect } = require("@playwright/test");

async function enrollmentStatus(request, operation, url, options, expected) {
  let status;
  try {
    const response = await request.fetch(url, { ...options, failOnStatusCode: false });
    status = response.status();
  } catch (error) {
    // Playwright can include Authorization and Cookie values in the original
    // exception. Only report a fixed label and never attach the original cause.
    const failure = error?.name === "TimeoutError" ? "request_timeout" : "transport_unavailable";
    throw new Error(operation + " " + failure);
  }
  expect(status, operation + " unexpected HTTP status").toBe(expected);
}

async function fillSecret(field, value) {
  try {
    await field.fill(value);
  } catch {
    throw new Error("A private enrollment field could not be filled.");
  }
}

async function readSignInToken(response) {
  try {
    const { access_token: token } = await response.json();
    if (typeof token === "string" && token) return token;
  } catch {
    // Invalid JSON exceptions can include the response body.
  }
  throw new Error("Sign-in did not return a usable access token.");
}

module.exports = { enrollmentStatus, fillSecret, readSignInToken };
