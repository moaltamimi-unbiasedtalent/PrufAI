// The positive API fixtures act as a cooperating browser client. Negative writer
// probes pass writerContext:false or explicit headers and never gain authority.
async function candidateMutationHeaders(page, method, path, headers) {
  if (!["POST", "PUT"].includes(method)) return {};
  const match = new URL(path, "http://127.0.0.1").pathname.match(
    /^\/api\/v1\/candidate\/exam-attempts\/([a-f0-9-]+)\/(?:start|submit|navigate|responses\/[^/]+)$/,
  );
  if (
    !match ||
    !headers?.Authorization ||
    !headers["X-PrufAI-Organisation-ID"] ||
    headers["X-PrufAI-Writer-ID"]
  )
    return {};
  try {
    const me = await page.request.get("/api/v1/auth/me", {
      headers,
      timeout: 10000,
      maxRedirects: 0,
    });
    if (me.status() !== 200) return {};
    const user = await me.json();
    const attemptId = match[1];
    const runtime = await page.request.get(
      `/api/v1/candidate/exam-attempts/${attemptId}/runtime`,
      { headers, timeout: 10000, maxRedirects: 0 },
    );
    if (runtime.status() !== 200) return {};
    const state = await runtime.json();
    if (!state.writer || !["READY", "IN_PROGRESS"].includes(state.status))
      return {};
    const clientId = await page.evaluate(
      async ({ userId, orgId, attemptId }) =>
        new Promise((resolve, reject) => {
          const name = `prufai-candidate-runtime-v2:${encodeURIComponent(userId)}:${encodeURIComponent(orgId)}`;
          const request = indexedDB.open(name, 5);
          request.onerror = () =>
            reject(new Error("Synthetic writer store unavailable"));
          request.onblocked = () =>
            reject(new Error("Synthetic writer upgrade blocked"));
          request.onupgradeneeded = () => {
            const db = request.result;
            if (!db.objectStoreNames.contains("response-outbox"))
              db.createObjectStore("response-outbox", {
                keyPath: "operationId",
              });
            for (const store of ["submission-fences", "writer-identities"])
              if (!db.objectStoreNames.contains(store))
                db.createObjectStore(store);
          };
          request.onsuccess = () => {
            const db = request.result,
              transaction = db.transaction("writer-identities", "readwrite"),
              store = transaction.objectStore("writer-identities"),
              value = store.get(attemptId);
            let marker;
            value.onsuccess = () => {
              marker = value.result ?? crypto.randomUUID();
              if (value.result === undefined) store.put(marker, attemptId);
            };
            transaction.oncomplete = () => {
              db.close();
              resolve(marker);
            };
            transaction.onerror = () => {
              db.close();
              reject(new Error("Synthetic writer transaction failed"));
            };
          };
        }),
      {
        userId: user.id,
        orgId: headers["X-PrufAI-Organisation-ID"],
        attemptId,
      },
    );
    const claimed = await page.request.post(
      `/api/v1/candidate/exam-attempts/${attemptId}/writer`,
      {
        headers,
        data: {
          client_id: clientId,
          expected_generation: state.writer.generation,
          acknowledge_takeover: false,
        },
        timeout: 10000,
        maxRedirects: 0,
      },
    );
    if (claimed.status() !== 200)
      throw new Error("Synthetic client did not own writer authority");
    const writer = await claimed.json();
    return {
      "X-PrufAI-Writer-ID": clientId,
      "X-PrufAI-Writer-Generation": String(writer.generation),
    };
  } catch {
    throw new Error(
      "The private writer fixture could not establish authority.",
    );
  }
}
module.exports = { candidateMutationHeaders };
