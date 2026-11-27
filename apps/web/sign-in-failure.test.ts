import { describe, expect, it } from "vitest";
import { SignInFailure, signInFailureMessage } from "./lib/sign-in-failure";

describe("sign-in diagnostic boundary", () => {
  it.each([false, true])(
    "never reads or copies unknown diagnostics (German: %s)",
    (german) => {
      const canary = "synthetic-private-transport-detail";
      const causes = [
        new TypeError(canary),
        new Error(canary),
        canary,
        null,
        {
          get message() {
            throw new Error("Diagnostic getter must not be called");
          },
        },
      ];
      const expected = signInFailureMessage(
        new SignInFailure("unconfirmed"),
        german,
      );
      for (const cause of causes) {
        expect(signInFailureMessage(cause, german)).toBe(expected);
        expect(signInFailureMessage(cause, german).includes(canary)).toBe(
          false,
        );
      }
    },
  );

  it("carries only a fixed category, without an original exception or payload", () => {
    const error = new SignInFailure("unavailable");
    expect(error.message).toBe("SIGN_IN_UNAVAILABLE");
    expect(error.cause).toBeUndefined();
    expect(Object.keys(error)).toEqual(["kind"]);
  });
});
