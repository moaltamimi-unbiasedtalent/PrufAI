export const ACCESS_TOKEN_KEY = "prufai_demo_access_token";

export function accessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

export function apiUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  return `${base}${path}`;
}

export function authenticatedFetch(path: string, init: RequestInit = {}) {
  const token = accessToken();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const organisationId = process.env.NEXT_PUBLIC_ORGANISATION_ID;
  if (organisationId && !headers.has("X-PrufAI-Organisation-ID")) {
    headers.set("X-PrufAI-Organisation-ID", organisationId);
  }
  return fetch(apiUrl(path), { ...init, headers, credentials: "include" });
}
