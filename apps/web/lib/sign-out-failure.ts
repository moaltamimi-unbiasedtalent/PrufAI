export type SignOutNotice = "unconfirmed" | "offline_cleanup_failed";

// Never retain the original transport, CSRF or storage exception.
export class SignOutFailure extends Error {
  constructor() {
    super("SIGN_OUT_UNCONFIRMED");
  }
}

export function signOutNoticeMessage(notice: SignOutNotice, german: boolean) {
  if (notice === "offline_cleanup_failed")
    return german
      ? "Sie sind auf dem Server abgemeldet. Offline-Daten konnten nicht gelöscht werden. Schließen Sie diesen Tab, bevor Sie das Gerät weitergeben."
      : "Signed out on the server. Offline data could not be cleared. Close this tab before sharing this device.";
  return german
    ? "Die Abmeldung konnte nicht bestätigt werden. Sie bleiben angemeldet. Bitte versuchen Sie die Abmeldung erneut, bevor Sie das Konto wechseln."
    : "Sign-out could not be confirmed. You remain signed in. Please retry signing out before switching accounts.";
}
