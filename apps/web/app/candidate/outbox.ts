export type CandidateOutboxOperation = {
  attemptId: string;
  bindingId: string;
  operationId: string;
  expectedRevision: number;
  payload: Record<string, unknown>;
  queuedAt: string;
};

const DATABASE = "prufai-candidate-runtime";
const STORE = "response-outbox";
const MAX_OPERATIONS = 100;

export function openCandidateOutbox(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore(STORE, { keyPath: "operationId" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function queueCandidateOperation(
  operation: CandidateOutboxOperation,
): Promise<void> {
  const database = await openCandidateOutbox();
  const transaction = database.transaction(STORE, "readwrite");
  const store = transaction.objectStore(STORE);
  const count = await new Promise<number>((resolve, reject) => {
    const request = store.count();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  if (count >= MAX_OPERATIONS)
    throw new Error("candidate response outbox is full");
  store.put(operation);
  await new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

export function removeCandidateOperation(
  database: IDBDatabase,
  operationId: string,
): void {
  database
    .transaction(STORE, "readwrite")
    .objectStore(STORE)
    .delete(operationId);
}

export async function syncCandidateOutbox(
  send: (
    operation: CandidateOutboxOperation,
  ) => Promise<"ack" | "conflict" | "expired">,
): Promise<{ acknowledged: number; conflicts: number }> {
  const database = await openCandidateOutbox();
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
  for (const operation of operations.sort((left, right) =>
    left.queuedAt.localeCompare(right.queuedAt),
  )) {
    const result = await send(operation);
    if (result === "ack" || result === "expired") {
      removeCandidateOperation(database, operation.operationId);
      acknowledged += 1;
    } else {
      conflicts += 1;
    }
  }
  return { acknowledged, conflicts };
}
