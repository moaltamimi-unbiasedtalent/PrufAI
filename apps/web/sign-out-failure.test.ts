import { describe, expect, it } from "vitest";
import { SignOutFailure, signOutNoticeMessage } from "./lib/sign-out-failure";

describe("sign-out diagnostic boundary", () => {
  it("throws only a fixed failure without original diagnostics or cause", () => {
    const error = new SignOutFailure();
    expect(error.message).toBe("SIGN_OUT_UNCONFIRMED");
    expect(error.cause).toBeUndefined();
    expect(Object.keys(error)).toEqual([]);
  });

  it.each([false, true])(
    "distinguishes unconfirmed revocation from failed offline cleanup (German: %s)",
    (german) => {
      expect(signOutNoticeMessage("unconfirmed", german)).toBe(
        german
          ? "Die Abmeldung konnte nicht bestätigt werden. Sie bleiben angemeldet. Bitte versuchen Sie die Abmeldung erneut, bevor Sie das Konto wechseln."
          : "Sign-out could not be confirmed. You remain signed in. Please retry signing out before switching accounts.",
      );
      expect(signOutNoticeMessage("offline_cleanup_failed", german)).toBe(
        german
          ? "Sie sind auf dem Server abgemeldet. Offline-Daten konnten nicht gelöscht werden. Schließen Sie diesen Tab, bevor Sie das Gerät weitergeben."
          : "Signed out on the server. Offline data could not be cleared. Close this tab before sharing this device.",
      );
    },
  );
});
