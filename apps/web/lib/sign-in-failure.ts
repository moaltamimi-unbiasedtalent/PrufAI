type SignInFailureKind = "credentials" | "unavailable" | "unconfirmed";

// Fixed categories only: transport errors and response bodies are never copied.
export class SignInFailure extends Error {
  constructor(readonly kind: SignInFailureKind) {
    super("SIGN_IN_" + kind.toUpperCase());
  }
}

export function signInFailureMessage(cause: unknown, german: boolean): string {
  const kind = cause instanceof SignInFailure ? cause.kind : "unconfirmed";
  if (kind === "credentials")
    return german
      ? "E-Mail-Adresse oder Passwort ist falsch, oder die Anmeldung ist vorübergehend begrenzt. Prüfen Sie Ihre Eingaben und versuchen Sie es später erneut."
      : "Email or password is incorrect, or sign in is temporarily rate limited. Check your entries and try again later.";
  if (kind === "unavailable")
    return german
      ? "Die Anmeldung ist vorübergehend nicht verfügbar. Bitte versuchen Sie es später erneut. Wenn das Problem bestehen bleibt, wenden Sie sich an Ihre Einrichtung."
      : "Sign-in is temporarily unavailable. Please try again later. If the problem persists, contact your institution.";
  return german
    ? "Die Anmeldung konnte nicht abgeschlossen werden. Bitte versuchen Sie es später erneut. Wenn das Problem bestehen bleibt, wenden Sie sich an Ihre Einrichtung."
    : "Sign-in could not be completed. Please try again later. If the problem persists, contact your institution.";
}
