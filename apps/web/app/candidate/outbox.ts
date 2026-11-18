export type CandidateOutboxOperation = {
  attemptId: string;
  bindingId: string;
  operationId: string;
  expectedRevision: number;
  payload: Record<string, unknown>;
  queuedAt: string;
};

export type CandidateAccountScope = { userId: string; organisationId: string };

const LEGACY_DATABASE = "prufai-candidate-runtime";
const STORE = "response-outbox";
const SUBMISSIONS = "submission-fences";
const WRITERS = "writer-identities";
const MAX_OPERATIONS = 100;
const MAX_PAYLOAD_BYTES = 64 * 1024;
const MAX_RETENTION_MS = 24 * 60 * 60 * 1000;
let activeAccount: string | null = null;
let accountGeneration = 0;
const openDatabases = new Map<IDBDatabase, string>();
let notificationSource: string | undefined;

function closeDatabase(database: IDBDatabase): void {
  database.close();
  openDatabases.delete(database);
}

export function lockActiveCandidateAccount(
  scope: CandidateAccountScope | null,
): void {
  const next = scope ? databaseName(scope) : null;
  if (next === activeAccount) return;
  accountGeneration += 1;
  activeAccount = next;
  for (const database of openDatabases.keys()) closeDatabase(database);
}

function requireActiveAccount(
  scope: CandidateAccountScope,
  generation = accountGeneration,
): void {
  if (
    databaseName(scope) !== activeAccount ||
    generation !== accountGeneration
  ) {
    throw new Error(
      "candidate account changed; queued response access was cancelled",
    );
  }
}

function databaseName(scope: CandidateAccountScope): string {
  if (!scope.userId || !scope.organisationId) {
    throw new Error("an authenticated candidate and organisation are required");
  }
  return `prufai-candidate-runtime-v2:${encodeURIComponent(scope.userId)}:${encodeURIComponent(scope.organisationId)}`;
}

function attemptResource(scope: CandidateAccountScope, attemptId: string) {
  return `${databaseName(scope)}:attempt:${encodeURIComponent(attemptId)}`;
}

export class CandidateOutboxBlocked extends Error {}

// Hold the exclusive lock across the shared read and the API action. Refuse a
// concurrent action instead of sending it later after the edit has resolved.
export async function withCandidateAttemptLock<T>(
  scope: CandidateAccountScope,
  attemptId: string,
  action: () => Promise<T>,
): Promise<T> {
  const generation = accountGeneration;
  requireActiveAccount(scope, generation);
  if (!navigator.locks)
    return Promise.reject(
      new CandidateOutboxBlocked("attempt lock unavailable"),
    );
  return navigator.locks.request(
    attemptResource(scope, attemptId),
    { ifAvailable: true },
    async (lock) => {
      if (!lock) throw new CandidateOutboxBlocked("attempt lock busy");
      requireActiveAccount(scope, generation);
      return action();
    },
  );
}

export function observeCandidateOutbox(
  scope: CandidateAccountScope,
  attemptId: string,
  changed: () => void,
): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  try {
    const channel = new BroadcastChannel(attemptResource(scope, attemptId));
    channel.onmessage = (event) => {
      if (event.data !== notificationSource) changed();
    };
    return () => channel.close();
  } catch {
    return () => {};
  }
}

export function notifyCandidateOutbox(
  scope: CandidateAccountScope,
  attemptId: string,
): void {
  if (typeof BroadcastChannel === "undefined") return;
  try {
    const channel = new BroadcastChannel(attemptResource(scope, attemptId));
    notificationSource ??= crypto.randomUUID();
    channel.postMessage(notificationSource);
    channel.close();
  } catch {
    // Focus and periodic reads can refresh stale UI. Actions always read IDB.
  }
}

function deleteDatabase(name: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.deleteDatabase(name);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () =>
      reject(new Error("candidate outbox is still open"));
  });
}

async function openCandidateOutbox(
  scope: CandidateAccountScope,
): Promise<IDBDatabase> {
  const name = databaseName(scope);
  const generation = accountGeneration;
  requireActiveAccount(scope, generation);
  // Legacy responses have no account identity; discard them without exposing
  // their plaintext to whichever candidate signs in next.
  await deleteDatabase(LEGACY_DATABASE);
  requireActiveAccount(scope, generation);
  return new Promise((resolve, reject) => {
    let blocked = false;
    // Version 5 requires provenance-aware fences. Refuse version-2/3/4
    // clients after upgrade; retain every existing operation and fence.
    const request = indexedDB.open(name, 5);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE))
        request.result.createObjectStore(STORE, { keyPath: "operationId" });
      if (!request.result.objectStoreNames.contains(SUBMISSIONS))
        request.result.createObjectStore(SUBMISSIONS);
      if (!request.result.objectStoreNames.contains(WRITERS))
        request.result.createObjectStore(WRITERS);
    };
    request.onblocked = () => {
      blocked = true;
      reject(new CandidateOutboxBlocked("outbox schema upgrade is blocked"));
    };
    request.onsuccess = () => {
      const database = request.result;
      if (blocked) {
        database.close();
        return;
      }
      try {
        requireActiveAccount(scope, generation);
        openDatabases.set(database, name);
        database.onversionchange = () => closeDatabase(database);
        resolve(database);
      } catch (error) {
        database.close();
        reject(error);
      }
    };
    request.onerror = () => reject(request.error);
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () =>
      reject(transaction.error ?? new Error("outbox update aborted"));
  });
}

export async function queueCandidateOperation(
  scope: CandidateAccountScope,
  operation: CandidateOutboxOperation,
  { exactRevision = false }: { exactRevision?: boolean } = {},
): Promise<void> {
  const generation = accountGeneration;
  requireActiveAccount(scope, generation);
  const payloadBytes = new TextEncoder().encode(
    JSON.stringify(operation.payload),
  ).byteLength;
  if (payloadBytes > MAX_PAYLOAD_BYTES)
    throw new Error("candidate response payload is too large");
  if (
    !Number.isFinite(Date.parse(operation.queuedAt)) ||
    Date.now() - Date.parse(operation.queuedAt) > MAX_RETENTION_MS
  )
    throw new Error("candidate response operation is expired");
  const database = await openCandidateOutbox(scope);
  try {
    const transaction = database.transaction(STORE, "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore(STORE);
    // Schedule the put in the request callback while the transaction is active.
    await new Promise<void>((resolve, reject) => {
      const request = store.getAll();
      request.onsuccess = () => {
        try {
          requireActiveAccount(scope, generation);
        } catch (error) {
          transaction.abort();
          reject(error);
          return;
        }
        const queued = request.result as CandidateOutboxOperation[];
        if (queued.length >= MAX_OPERATIONS) {
          transaction.abort();
          reject(new Error("candidate response outbox is full"));
          return;
        }
        const previous = queued.filter(
          (item) =>
            item.attemptId === operation.attemptId &&
            item.bindingId === operation.bindingId,
        );
        const expectedRevision = exactRevision
          ? operation.expectedRevision
          : previous.reduce(
              (next, item) => Math.max(next, item.expectedRevision + 1),
              operation.expectedRevision,
            );
        store.put({ ...operation, expectedRevision });
        resolve();
      };
      request.onerror = () => reject(request.error);
    }).catch(async (error: unknown) => {
      await complete.catch(() => undefined);
      throw error;
    });
    await complete;
  } finally {
    closeDatabase(database);
  }
}

async function removeCandidateOperation(
  database: IDBDatabase,
  operationId: string,
): Promise<void> {
  const transaction = database.transaction(STORE, "readwrite");
  const complete = transactionComplete(transaction);
  transaction.objectStore(STORE).delete(operationId);
  await complete;
}

export async function discardCandidateOperations(
  scope: CandidateAccountScope,
  attemptId: string,
  operationIds: ReadonlySet<string>,
): Promise<void> {
  const generation = accountGeneration;
  const database = await openCandidateOutbox(scope);
  try {
    const transaction = database.transaction(STORE, "readwrite");
    const complete = transactionComplete(transaction);
    const cursor = transaction.objectStore(STORE).openCursor();
    cursor.onsuccess = () => {
      try {
        requireActiveAccount(scope, generation);
      } catch {
        transaction.abort();
        return;
      }
      const row = cursor.result;
      if (!row) return;
      const operation = row.value as CandidateOutboxOperation;
      if (
        operation.attemptId === attemptId &&
        operationIds.has(operation.operationId)
      )
        row.delete();
      row.continue();
    };
    await complete;
  } finally {
    closeDatabase(database);
  }
}

export async function clearAccountOutbox(
  userId: string,
  organisationId: string,
): Promise<void> {
  const name = databaseName({ userId, organisationId });
  if (name === activeAccount) lockActiveCandidateAccount(null);
  for (const [database, owner] of openDatabases) {
    if (owner === name) closeDatabase(database);
  }
  await deleteDatabase(name);
  await deleteDatabase(LEGACY_DATABASE);
}

function readOperations(
  database: IDBDatabase,
): Promise<CandidateOutboxOperation[]> {
  return new Promise((resolve, reject) => {
    const request = database
      .transaction(STORE, "readonly")
      .objectStore(STORE)
      .getAll();
    request.onsuccess = () =>
      resolve(request.result as CandidateOutboxOperation[]);
    request.onerror = () => reject(request.error);
  });
}

function isExpired(operation: CandidateOutboxOperation): boolean {
  return Date.now() - Date.parse(operation.queuedAt) > MAX_RETENTION_MS;
}

export async function unresolvedCandidateBindings(
  scope: CandidateAccountScope,
  attemptId: string,
): Promise<ReadonlySet<string>> {
  return (await readCandidateAttemptBarrier(scope, attemptId)).bindingIds;
}

export async function readCandidateAttemptBarrier(
  scope: CandidateAccountScope,
  attemptId: string,
): Promise<{ bindingIds: ReadonlySet<string>; submissionStarted: boolean }> {
  const generation = accountGeneration;
  const database = await openCandidateOutbox(scope);
  try {
    const transaction = database.transaction([STORE, SUBMISSIONS], "readonly");
    const complete = transactionComplete(transaction);
    const operations = transaction.objectStore(STORE).getAll();
    const submission = transaction.objectStore(SUBMISSIONS).getKey(attemptId);
    await complete;
    requireActiveAccount(scope, generation);
    return {
      bindingIds: new Set(
        (operations.result as CandidateOutboxOperation[])
          .filter((operation) => operation.attemptId === attemptId)
          .map(({ bindingId }) => bindingId),
      ),
      submissionStarted: submission.result !== undefined,
    };
  } finally {
    closeDatabase(database);
  }
}

type SubmissionProvenance =
  | { provenance: "KNOWN"; generationCeiling: number }
  | { provenance: "LEGACY_UNKNOWN" };

function validWriterGeneration(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function knownGenerationCeiling(value: unknown): number | null {
  if (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    "submissionId" in value &&
    typeof value.submissionId === "string" &&
    /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(
      value.submissionId,
    ) &&
    "recoverable" in value &&
    typeof value.recoverable === "boolean" &&
    "provenance" in value &&
    value.provenance === "KNOWN" &&
    "generationCeiling" in value &&
    validWriterGeneration(value.generationCeiling)
  )
    return value.generationCeiling;
  return null;
}

function submissionProvenance(
  previous: unknown,
  present: boolean,
  writerGeneration: number | undefined,
): SubmissionProvenance {
  const ceiling = knownGenerationCeiling(previous);
  if (
    validWriterGeneration(writerGeneration) &&
    (!present || ceiling !== null)
  ) {
    return {
      provenance: "KNOWN",
      generationCeiling: Math.max(
        ceiling ?? writerGeneration,
        writerGeneration,
      ),
    };
  }
  // Untagged v4 records may already contain a generation stamped by a retry.
  // Only a new, absent fence can establish known lineage; uncertainty is sticky.
  return { provenance: "LEGACY_UNKNOWN" };
}

// This fence survives a closed tab or an ambiguous submission response. While
// present, the same submission may be retried, with answer edits kept blocked.
// The caller holds the attempt lock while persisting the fence and submitting.
export async function fenceCandidateSubmission(
  scope: CandidateAccountScope,
  attemptId: string,
  writerGeneration?: number,
): Promise<string> {
  const generation = accountGeneration;
  const submissionId = crypto.randomUUID();
  const database = await openCandidateOutbox(scope);
  try {
    requireActiveAccount(scope, generation);
    const transaction = database.transaction(SUBMISSIONS, "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore(SUBMISSIONS);
    const previous = store.get(attemptId);
    const key = store.getKey(attemptId);
    let ready = 0;
    const persist = () => {
      if (++ready !== 2) return;
      try {
        requireActiveAccount(scope, generation);
        const present = key.result !== undefined;
        store.put(
          {
            submissionId,
            recoverable: !present,
            ...submissionProvenance(previous.result, present, writerGeneration),
          },
          attemptId,
        );
      } catch {
        transaction.abort();
      }
    };
    previous.onsuccess = persist;
    key.onsuccess = persist;
    await complete;
    requireActiveAccount(scope, generation);
    notifyCandidateOutbox(scope, attemptId);
    return submissionId;
  } finally {
    closeDatabase(database);
  }
}

// The caller retains the exclusive attempt lock and has verified a definite
// rejection and an authenticated IN_PROGRESS runtime for this submission.
export async function releaseRejectedCandidateSubmission(
  scope: CandidateAccountScope,
  attemptId: string,
  submissionId: string,
): Promise<boolean> {
  const generation = accountGeneration;
  const database = await openCandidateOutbox(scope);
  try {
    const transaction = database.transaction([STORE, SUBMISSIONS], "readwrite");
    const complete = transactionComplete(transaction);
    const fences = transaction.objectStore(SUBMISSIONS);
    const fence = fences.get(attemptId);
    const operations = transaction.objectStore(STORE).getAll();
    let fenceReady = false;
    let operationsReady = false;
    let released = false;
    const check = () => {
      if (!fenceReady || !operationsReady) return;
      try {
        requireActiveAccount(scope, generation);
        const value: unknown = fence.result;
        if (
          typeof value === "object" &&
          value !== null &&
          "submissionId" in value &&
          value.submissionId === submissionId &&
          "recoverable" in value &&
          value.recoverable === true &&
          !(operations.result as CandidateOutboxOperation[]).some(
            (op) => op.attemptId === attemptId,
          )
        ) {
          fences.delete(attemptId);
          released = true;
        }
      } catch {
        transaction.abort();
      }
    };
    fence.onsuccess = () => {
      fenceReady = true;
      check();
    };
    operations.onsuccess = () => {
      operationsReady = true;
      check();
    };
    await complete;
    requireActiveAccount(scope, generation);
    if (released) notifyCandidateOutbox(scope, attemptId);
    return released;
  } finally {
    closeDatabase(database);
  }
}

export async function syncCandidateOutbox(
  scope: CandidateAccountScope,
  send: (
    operation: CandidateOutboxOperation,
  ) => Promise<"ack" | "conflict" | "expired" | "retry">,
  attemptId?: string,
  { retainUnresolved = false }: { retainUnresolved?: boolean } = {},
): Promise<{ acknowledged: number; conflicts: number; retryNeeded?: boolean }> {
  const generation = accountGeneration;
  requireActiveAccount(scope, generation);
  const database = await openCandidateOutbox(scope);
  try {
    const operations = await readOperations(database);
    let acknowledged = 0;
    let conflicts = 0;
    const blockedBindings = new Set<string>();
    for (const operation of operations.sort((left, right) =>
      left.queuedAt.localeCompare(right.queuedAt),
    )) {
      requireActiveAccount(scope, generation);
      if (attemptId && operation.attemptId !== attemptId) continue;
      if (!retainUnresolved && isExpired(operation)) {
        await removeCandidateOperation(database, operation.operationId);
        continue;
      }
      const bindingKey = `${operation.attemptId}:${operation.bindingId}`;
      if (blockedBindings.has(bindingKey)) {
        conflicts += 1;
        continue;
      }
      const result = await send(operation);
      requireActiveAccount(scope, generation);
      if (result === "retry")
        return { acknowledged, conflicts, retryNeeded: true };
      if (result === "ack" || result === "expired") {
        await removeCandidateOperation(database, operation.operationId);
        acknowledged += 1;
      } else {
        blockedBindings.add(bindingKey);
        conflicts += 1;
      }
    }
    return { acknowledged, conflicts };
  } finally {
    closeDatabase(database);
  }
}

export async function candidateWriterIdentity(
  scope: CandidateAccountScope,
  attemptId: string,
): Promise<string> {
  const generation = accountGeneration;
  const database = await openCandidateOutbox(scope);
  try {
    const transaction = database.transaction(WRITERS, "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore(WRITERS);
    const value = store.get(attemptId);
    let identity = "";
    value.onsuccess = () => {
      try {
        requireActiveAccount(scope, generation);
        if (value.result === undefined) {
          identity = crypto.randomUUID();
          store.put(identity, attemptId);
        } else if (
          typeof value.result === "string" &&
          /^[a-f0-9-]{36}$/.test(value.result)
        ) {
          identity = value.result;
        } else throw new CandidateOutboxBlocked("writer identity is invalid");
      } catch {
        transaction.abort();
      }
    };
    await complete;
    requireActiveAccount(scope, generation);
    if (!identity)
      throw new CandidateOutboxBlocked("writer identity is unavailable");
    return identity;
  } finally {
    closeDatabase(database);
  }
}

// A confirmed higher server generation fences every older in-flight request.
// The caller holds the attempt lock and has authenticated IN_PROGRESS state at
// this owned generation. Same-generation and legacy uncertainty remain fenced.
export async function releaseSupersededCandidateSubmission(
  scope: CandidateAccountScope,
  attemptId: string,
  ownedGeneration: number,
): Promise<boolean> {
  if (!validWriterGeneration(ownedGeneration)) return false;
  const generation = accountGeneration;
  const database = await openCandidateOutbox(scope);
  try {
    const transaction = database.transaction([STORE, SUBMISSIONS], "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore(SUBMISSIONS);
    const fence = store.get(attemptId),
      operations = transaction.objectStore(STORE).getAll();
    let ready = 0,
      released = false;
    const inspect = () => {
      if (++ready !== 2) return;
      try {
        requireActiveAccount(scope, generation);
        const ceiling = knownGenerationCeiling(fence.result);
        if (
          ceiling !== null &&
          ceiling < ownedGeneration &&
          !(operations.result as CandidateOutboxOperation[]).some(
            (op) => op.attemptId === attemptId,
          )
        ) {
          store.delete(attemptId);
          released = true;
        }
      } catch {
        transaction.abort();
      }
    };
    fence.onsuccess = inspect;
    operations.onsuccess = inspect;
    await complete;
    requireActiveAccount(scope, generation);
    if (released) notifyCandidateOutbox(scope, attemptId);
    return released;
  } finally {
    closeDatabase(database);
  }
}
