// Access tokens stay in memory; the server owns the HttpOnly refresh cookie.
let token: string | null = null;
let organisationId: string | null = null;
let generation = 0;
let refreshPending: Promise<boolean> | null = null;
let expired: (() => void) | null = null;

export function accessToken(): string | null {
  return token;
}
export function setSessionToken(value: string | null) {
  generation += 1;
  token = value;
  organisationId = null;
}
export function setOrganisationContext(value: string | null) {
  if (value !== organisationId) generation += 1;
  organisationId = value;
}
export function onSessionExpired(handler: (() => void) | null) {
  expired = handler;
}

export function apiUrl(path: string): string {
  if (!path.startsWith("/api/v1/") || path.includes("\\")) {
    throw new Error("Only same-origin PrüfAI API paths are supported.");
  }
  return path;
}

export function authFetch(path: string, init: RequestInit = {}) {
  return fetch(apiUrl(path), {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
  });
}

export function withSessionLock<T>(operation: () => Promise<T>): Promise<T> {
  // Serialize rotating cookie operations across same-origin tabs where Web Locks
  // are supported. The in-module pending promise also coalesces React effects.
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request("prufai-session-cookie", operation);
  }
  return operation();
}

export async function csrfToken(): Promise<string | null> {
  const response = await authFetch("/api/v1/auth/csrf");
  if (!response.ok) return null;
  const data = (await response.json()) as { csrf_token: string };
  return data.csrf_token;
}

export function refreshAccessToken(): Promise<boolean> {
  if (refreshPending) return refreshPending;
  const startedGeneration = generation;
  const pending = withSessionLock(async () => {
    try {
      if (startedGeneration !== generation) return false;
      const csrf = await csrfToken();
      if (startedGeneration !== generation || !csrf) return false;
      const response = await authFetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "X-PrufAI-CSRF": csrf },
      });
      if (!response.ok) return false;
      const data = (await response.json()) as { access_token: string };
      if (startedGeneration !== generation) return false;
      token = data.access_token;
      return true;
    } catch {
      return false;
    }
  });
  refreshPending = pending;
  void pending.finally(() => {
    if (refreshPending === pending) refreshPending = null;
  });
  return pending;
}

export async function authenticatedFetch(path: string, init: RequestInit = {}) {
  const startedGeneration = generation;
  const sentToken = token;
  const request = async () => {
    const headers = new Headers(init.headers);
    if (init.body && !headers.has("Content-Type"))
      headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (
      path.startsWith("/api/v1/auth/") ||
      path.startsWith("/api/v1/scoped-source-review/") ||
      path === "/api/v1/platform/control-center" ||
      path.startsWith("/api/v1/platform/control-center/") ||
      path === "/api/v1/platform/evaluation" ||
      path.startsWith("/api/v1/platform/evaluation/") ||
      path === "/api/v1/platform/feedback" ||
      path.startsWith("/api/v1/platform/feedback/") ||
      path === "/api/v1/platform/standards" ||
      path.startsWith("/api/v1/platform/standards/") ||
      path === "/api/v1/platform/standard-packs" ||
      path.startsWith("/api/v1/platform/standard-packs/") ||
      path === "/api/v1/platform/standard-pack-versions" ||
      path.startsWith("/api/v1/platform/standard-pack-versions/")
    )
      headers.delete("X-PrufAI-Organisation-ID");
    else if (organisationId)
      headers.set("X-PrufAI-Organisation-ID", organisationId);
    if (path === "/api/v1/auth/logout") {
      return withSessionLock(async () => {
        if (startedGeneration !== generation)
          throw new Error(
            "The account or organisation changed. Reload this page.",
          );
        const csrf = await csrfToken();
        if (startedGeneration !== generation)
          throw new Error(
            "The account or organisation changed. Reload this page.",
          );
        if (csrf) headers.set("X-PrufAI-CSRF", csrf);
        return authFetch(path, { ...init, headers });
      });
    }
    return authFetch(path, { ...init, headers });
  };
  let response = await request();
  if (startedGeneration !== generation)
    throw new Error("The account or organisation changed. Reload this page.");
  if (response.status === 401) {
    const refreshed =
      (token !== sentToken && token !== null) || (await refreshAccessToken());
    if (startedGeneration !== generation)
      throw new Error("The account or organisation changed. Reload this page.");
    if (refreshed) {
      response = await request();
    }
    if (startedGeneration !== generation)
      throw new Error("The account or organisation changed. Reload this page.");
    if (response.status === 401 || !refreshed) {
      setSessionToken(null);
      expired?.();
    }
  }
  return response;
}
