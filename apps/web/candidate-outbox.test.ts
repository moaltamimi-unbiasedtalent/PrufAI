import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAccountOutbox,
  lockActiveCandidateAccount,
  queueCandidateOperation,
  syncCandidateOutbox,
  type CandidateAccountScope,
  type CandidateOutboxOperation,
} from "./app/candidate/outbox";

// A request/transaction test double keeps persistence semantics observable; the
// browser walkthrough separately exercises the actual IndexedDB implementation.
function indexedDbDouble() {
  type StoredDatabase = {
    rows: Map<string, CandidateOutboxOperation>;
    connections: number;
  };
  const databases = new Map<string, StoredDatabase>();
  const request = <T>(result: T) => ({
    result,
    error: null,
    onsuccess: null as null | (() => void),
    onerror: null as null | (() => void),
    onupgradeneeded: null as null | (() => void),
    onblocked: null as null | (() => void),
  });
  return {
    databases,
    open(name: string) {
      const isNew = !databases.has(name);
      if (isNew) databases.set(name, { rows: new Map(), connections: 0 });
      const stored = databases.get(name)!;
      stored.connections += 1;
      let closed = false;
      const database = {
        createObjectStore: () => undefined,
        close: () => {
          if (!closed) stored.connections -= 1;
          closed = true;
        },
        transaction: () => {
          const transaction = {
            error: null,
            oncomplete: null as null | (() => void),
            onerror: null as null | (() => void),
            onabort: null as null | (() => void),
            abort: () => queueMicrotask(() => transaction.onabort?.()),
            objectStore: () => ({
              getAll: () => {
                const result = request(
                  [...stored.rows.values()].map((row) => structuredClone(row)),
                );
                queueMicrotask(() => result.onsuccess?.());
                return result;
              },
              put: (operation: CandidateOutboxOperation) => {
                stored.rows.set(
                  operation.operationId,
                  structuredClone(operation),
                );
                setTimeout(() => transaction.oncomplete?.(), 0);
              },
              delete: (operationId: string) => {
                stored.rows.delete(operationId);
                setTimeout(() => transaction.oncomplete?.(), 0);
              },
            }),
          };
          return transaction;
        },
      };
      const result = request(database);
      queueMicrotask(() => {
        if (isNew) result.onupgradeneeded?.();
        result.onsuccess?.();
      });
      return result;
    },
    deleteDatabase(name: string) {
      const result = request(undefined);
      queueMicrotask(() => {
        if (databases.get(name)?.connections) {
          result.onblocked?.();
        } else {
          databases.delete(name);
          result.onsuccess?.();
        }
      });
      return result;
    },
  };
}

const alice: CandidateAccountScope = {
  userId: "alice",
  organisationId: "academy",
};
const bob: CandidateAccountScope = { userId: "bob", organisationId: "academy" };
const otherAcademy: CandidateAccountScope = {
  userId: "alice",
  organisationId: "other",
};
const operation = (
  operationId: string,
  text: string,
): CandidateOutboxOperation => ({
  operationId,
  attemptId: "attempt",
  bindingId: "question",
  expectedRevision: 0,
  payload: { text },
  queuedAt: new Date().toISOString(),
});

describe("account-bound candidate outbox", () => {
  let indexedDb: ReturnType<typeof indexedDbDouble>;
  beforeEach(() => {
    lockActiveCandidateAccount(null);
    indexedDb = indexedDbDouble();
    vi.stubGlobal("indexedDB", indexedDb);
  });
  afterEach(() => {
    lockActiveCandidateAccount(null);
    vi.unstubAllGlobals();
  });

  it("never exposes another user or organisation's queued plaintext to sync", async () => {
    lockActiveCandidateAccount(alice);
    await queueCandidateOperation(
      alice,
      operation("a", "Alice private response"),
    );
    lockActiveCandidateAccount(bob);
    await queueCandidateOperation(bob, operation("b", "Bob response"));
    lockActiveCandidateAccount(otherAcademy);
    await queueCandidateOperation(
      otherAcademy,
      operation("c", "Other academy response"),
    );
    const bobSend = vi.fn(async () => "ack" as const);
    lockActiveCandidateAccount(bob);
    expect(await syncCandidateOutbox(bob, bobSend)).toEqual({
      acknowledged: 1,
      conflicts: 0,
    });
    expect(bobSend.mock.calls).toHaveLength(1);
    expect(bobSend).toHaveBeenCalledWith(
      expect.objectContaining({ payload: { text: "Bob response" } }),
    );
    const aliceSend = vi.fn(async () => "ack" as const);
    lockActiveCandidateAccount(alice);
    await syncCandidateOutbox(alice, aliceSend);
    expect(aliceSend).toHaveBeenCalledWith(
      expect.objectContaining({ payload: { text: "Alice private response" } }),
    );
    expect(indexedDb.databases.size).toBe(3);
  });

  it("logout removes only that account's responses and preserves another account's queue", async () => {
    lockActiveCandidateAccount(alice);
    await queueCandidateOperation(alice, operation("a", "private"));
    lockActiveCandidateAccount(bob);
    await queueCandidateOperation(bob, operation("b", "other"));
    await clearAccountOutbox(alice.userId, alice.organisationId);
    const aliceSend = vi.fn(async () => "ack" as const);
    lockActiveCandidateAccount(alice);
    await syncCandidateOutbox(alice, aliceSend);
    expect(aliceSend).not.toHaveBeenCalled();
    const bobSend = vi.fn(async () => "ack" as const);
    lockActiveCandidateAccount(bob);
    await syncCandidateOutbox(bob, bobSend);
    expect(bobSend).toHaveBeenCalledWith(
      expect.objectContaining({ operationId: "b" }),
    );
  });

  it("discards the legacy unscoped database without syncing its contents", async () => {
    lockActiveCandidateAccount(alice);
    indexedDb.databases.set("prufai-candidate-runtime", {
      rows: new Map([["legacy", operation("legacy", "unbound private text")]]),
      connections: 0,
    });
    const send = vi.fn(async () => "ack" as const);
    await syncCandidateOutbox(alice, send);
    expect(send).not.toHaveBeenCalled();
    expect(indexedDb.databases.has("prufai-candidate-runtime")).toBe(false);
  });

  it("chains offline revisions and retains subsequent writes after a conflict", async () => {
    lockActiveCandidateAccount(alice);
    await queueCandidateOperation(alice, operation("one", "first"));
    await queueCandidateOperation(alice, operation("two", "second"));
    const conflict = vi.fn(async () => "conflict" as const);
    expect(await syncCandidateOutbox(alice, conflict)).toEqual({
      acknowledged: 0,
      conflicts: 2,
    });
    expect(conflict).toHaveBeenCalledTimes(1);
    const accepted: number[] = [];
    await syncCandidateOutbox(alice, async (item) => {
      accepted.push(item.expectedRevision);
      return "ack";
    });
    expect(accepted).toEqual([0, 1]);
    const send = vi.fn(async () => "ack" as const);
    await syncCandidateOutbox(alice, send);
    expect(send).not.toHaveBeenCalled();
  });

  it("refuses unauthenticated scope and expired operations", async () => {
    lockActiveCandidateAccount(alice);
    await expect(
      queueCandidateOperation(
        { userId: "", organisationId: "academy" },
        operation("a", "private"),
      ),
    ).rejects.toThrow("authenticated candidate");
    await expect(
      queueCandidateOperation(alice, {
        ...operation("b", "old"),
        queuedAt: "invalid",
      }),
    ).rejects.toThrow("expired");
  });

  it("cancels in-flight access before it can reopen a purged account queue", async () => {
    lockActiveCandidateAccount(alice);
    const pending = queueCandidateOperation(
      alice,
      operation("pending", "private"),
    );
    lockActiveCandidateAccount(null);
    const rejected = expect(pending).rejects.toThrow("account changed");
    await clearAccountOutbox(alice.userId, alice.organisationId);
    await rejected;
    lockActiveCandidateAccount(bob);
    const send = vi.fn(async () => "ack" as const);
    await syncCandidateOutbox(bob, send);
    expect(send).not.toHaveBeenCalled();
    await expect(syncCandidateOutbox(alice, send)).rejects.toThrow(
      "account changed",
    );
    expect([...indexedDb.databases.keys()]).not.toContain(
      "prufai-candidate-runtime-v2:alice:academy",
    );
  });
});
