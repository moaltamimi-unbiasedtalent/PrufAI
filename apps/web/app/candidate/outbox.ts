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
const MAX_OPERATIONS = 100;
const MAX_PAYLOAD_BYTES = 64 * 1024;
const MAX_RETENTION_MS = 24 * 60 * 60 * 1000;
let activeAccount: string | null = null;
let accountGeneration = 0;
const openDatabases = new Map<IDBDatabase, string>();

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
    const request = indexedDB.open(name, 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore(STORE, { keyPath: "operationId" });
    request.onsuccess = () => {
      const database = request.result;
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

export async function syncCandidateOutbox(
  scope: CandidateAccountScope,
  send: (
    operation: CandidateOutboxOperation,
  ) => Promise<"ack" | "conflict" | "expired" | "retry">,
  attemptId?: string,
): Promise<{ acknowledged: number; conflicts: number; retryNeeded?: boolean }> {
  const generation = accountGeneration;
  requireActiveAccount(scope, generation);
  const database = await openCandidateOutbox(scope);
  try {
    const operations = await new Promise<CandidateOutboxOperation[]>(
      (resolve, reject) => {
        const request = database
          .transaction(STORE, "readonly")
          .objectStore(STORE)
          .getAll();
        request.onsuccess = () =>
          resolve(request.result as CandidateOutboxOperation[]);
        request.onerror = () => reject(request.error);
      },
    );
    let acknowledged = 0;
    let conflicts = 0;
    const blockedBindings = new Set<string>();
    for (const operation of operations.sort((left, right) =>
      left.queuedAt.localeCompare(right.queuedAt),
    )) {
      requireActiveAccount(scope, generation);
      if (attemptId && operation.attemptId !== attemptId) continue;
      if (Date.now() - Date.parse(operation.queuedAt) > MAX_RETENTION_MS) {
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
