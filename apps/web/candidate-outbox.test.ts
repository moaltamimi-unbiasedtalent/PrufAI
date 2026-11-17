import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAccountOutbox,
  fenceCandidateSubmission,
  lockActiveCandidateAccount,
  queueCandidateOperation,
  readCandidateAttemptBarrier,
  releaseRejectedCandidateSubmission,
  syncCandidateOutbox,
  unresolvedCandidateBindings,
  withCandidateAttemptLock,
  type CandidateAccountScope,
  type CandidateOutboxOperation,
} from "./app/candidate/outbox";

// A request/transaction test double keeps persistence semantics observable; the
// browser walkthrough separately exercises the actual IndexedDB implementation.
function indexedDbDouble() {
  type StoredDatabase = {
    rows: Map<string, CandidateOutboxOperation>;
    connections: number;
    submissions?: Map<string, unknown>;
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
        objectStoreNames: { contains: () => false },
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
            objectStore: (name: string) => ({
              get: (key: string) => {
                const result = request(stored.submissions?.get(key));
                queueMicrotask(() => result.onsuccess?.());
                setTimeout(() => transaction.oncomplete?.(), 0);
                return result;
              },
              getAll: () => {
                const result = request(
                  [...stored.rows.values()].map((row) => structuredClone(row)),
                );
                queueMicrotask(() => result.onsuccess?.());
                return result;
              },
              put: (
                operation:
                  | CandidateOutboxOperation
                  | boolean
                  | { submissionId: string; recoverable: boolean },
                key: string,
              ) => {
                if (name === "submission-fences") {
                  stored.submissions ??= new Map();
                  stored.submissions.set(key, structuredClone(operation));
                } else if (
                  typeof operation !== "boolean" &&
                  "operationId" in operation
                ) {
                  stored.rows.set(
                    operation.operationId,
                    structuredClone(operation),
                  );
                }
                setTimeout(() => transaction.oncomplete?.(), 0);
              },
              delete: (operationId: string) => {
                if (name === "submission-fences")
                  stored.submissions?.delete(operationId);
                else stored.rows.delete(operationId);
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

  it("reports distinct unresolved bindings scoped to the active account and attempt", async () => {
    lockActiveCandidateAccount(alice);
    const first = operation("one", "first");
    await queueCandidateOperation(alice, first);
    await queueCandidateOperation(alice, operation("two", "replacement"));
    await queueCandidateOperation(alice, {
      ...operation("three", "different attempt"),
      attemptId: "other-attempt",
      bindingId: "other-question",
    });
    expect(await unresolvedCandidateBindings(alice, first.attemptId)).toEqual(
      new Set([first.bindingId]),
    );
    expect(await unresolvedCandidateBindings(alice, "other-attempt")).toEqual(
      new Set(["other-question"]),
    );
    lockActiveCandidateAccount(bob);
    await expect(
      unresolvedCandidateBindings(alice, first.attemptId),
    ).rejects.toThrow("candidate account changed");
    expect(await unresolvedCandidateBindings(bob, first.attemptId)).toEqual(
      new Set(),
    );
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

  it("retains an aged unresolved Berlin operation and retries its original identity", async () => {
    lockActiveCandidateAccount(alice);
    const pending = operation("original", "answer");
    await queueCandidateOperation(alice, pending);
    const now = vi
      .spyOn(Date, "now")
      .mockReturnValue(Date.now() + 48 * 60 * 60 * 1000);
    try {
      expect(
        await unresolvedCandidateBindings(alice, pending.attemptId),
      ).toEqual(new Set([pending.bindingId]));
      const send = vi.fn(async () => "retry" as const);
      expect(
        await syncCandidateOutbox(alice, send, pending.attemptId, {
          retainUnresolved: true,
        }),
      ).toMatchObject({ retryNeeded: true });
      expect(send).toHaveBeenCalledExactlyOnceWith(pending);
      expect(
        await unresolvedCandidateBindings(alice, pending.attemptId),
      ).toEqual(new Set([pending.bindingId]));
    } finally {
      now.mockRestore();
    }
  });

  it("scopes a submission fence to its attempt, account and organisation", async () => {
    lockActiveCandidateAccount(alice);
    await fenceCandidateSubmission(alice, "attempt");
    expect(await readCandidateAttemptBarrier(alice, "attempt")).toMatchObject({
      submissionStarted: true,
    });
    expect(await readCandidateAttemptBarrier(alice, "another")).toMatchObject({
      submissionStarted: false,
    });
    lockActiveCandidateAccount(otherAcademy);
    expect(
      await readCandidateAttemptBarrier(otherAcademy, "attempt"),
    ).toMatchObject({ submissionStarted: false });
    lockActiveCandidateAccount(bob);
    expect(await readCandidateAttemptBarrier(bob, "attempt")).toMatchObject({
      submissionStarted: false,
    });
    await expect(readCandidateAttemptBarrier(alice, "attempt")).rejects.toThrow(
      "account changed",
    );
  });

  it("refuses an attempt action when the browser cannot acquire a lock", async () => {
    lockActiveCandidateAccount(alice);
    vi.stubGlobal("navigator", {});
    const action = vi.fn(async () => "sent");
    await expect(
      withCandidateAttemptLock(alice, "attempt", action),
    ).rejects.toThrow("lock unavailable");
    expect(action).not.toHaveBeenCalled();
  });

  it("clears only the matching recoverable fence and retains unrelated answers", async () => {
    lockActiveCandidateAccount(alice);
    const submissionId = await fenceCandidateSubmission(alice, "attempt");
    await queueCandidateOperation(alice, {
      ...operation("unrelated", "another attempt"),
      attemptId: "another",
    });
    expect(
      await releaseRejectedCandidateSubmission(alice, "attempt", submissionId),
    ).toBe(true);
    expect(await readCandidateAttemptBarrier(alice, "attempt")).toMatchObject({
      submissionStarted: false,
    });
    expect(await unresolvedCandidateBindings(alice, "another")).toEqual(
      new Set(["question"]),
    );
  });

  it("refuses an older rejection and keeps the newer fence", async () => {
    lockActiveCandidateAccount(alice);
    const old = await fenceCandidateSubmission(alice, "attempt");
    const newer = await fenceCandidateSubmission(alice, "attempt");
    expect(newer).not.toBe(old);
    expect(
      await releaseRejectedCandidateSubmission(alice, "attempt", old),
    ).toBe(false);
    expect(
      await releaseRejectedCandidateSubmission(alice, "attempt", newer),
    ).toBe(false);
    expect(await readCandidateAttemptBarrier(alice, "attempt")).toMatchObject({
      submissionStarted: true,
    });
  });

  it("keeps legacy boolean fences conservative through a new rejected retry", async () => {
    lockActiveCandidateAccount(alice);
    await readCandidateAttemptBarrier(alice, "attempt");
    const database = [...indexedDb.databases.values()][0];
    database.submissions = new Map([["attempt", true]]);
    expect(
      await releaseRejectedCandidateSubmission(alice, "attempt", "unknown"),
    ).toBe(false);
    const retry = await fenceCandidateSubmission(alice, "attempt");
    expect(
      await releaseRejectedCandidateSubmission(alice, "attempt", retry),
    ).toBe(false);
    expect(await readCandidateAttemptBarrier(alice, "attempt")).toMatchObject({
      submissionStarted: true,
    });
  });

  it("refuses recovery when this attempt has unresolved answers or the account changes", async () => {
    lockActiveCandidateAccount(alice);
    const submissionId = await fenceCandidateSubmission(alice, "attempt");
    await queueCandidateOperation(alice, operation("pending", "unconfirmed"));
    expect(
      await releaseRejectedCandidateSubmission(alice, "attempt", submissionId),
    ).toBe(false);
    expect(await unresolvedCandidateBindings(alice, "attempt")).toEqual(
      new Set(["question"]),
    );
    const pending = releaseRejectedCandidateSubmission(
      alice,
      "attempt",
      submissionId,
    );
    lockActiveCandidateAccount(bob);
    await expect(pending).rejects.toThrow("account changed");
    lockActiveCandidateAccount(alice);
    expect(await readCandidateAttemptBarrier(alice, "attempt")).toMatchObject({
      submissionStarted: true,
    });
  });

  it("refuses competing actions and isolates locks by account and attempt", async () => {
    lockActiveCandidateAccount(alice);
    const held = new Set<string>();
    vi.stubGlobal("navigator", {
      locks: {
        request: async (
          resource: string,
          options: { ifAvailable: boolean },
          action: (lock: object | null) => Promise<unknown>,
        ) => {
          expect(options).toEqual({ ifAvailable: true });
          if (held.has(resource)) return action(null);
          held.add(resource);
          try {
            return await action({});
          } finally {
            held.delete(resource);
          }
        },
      },
    });
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const running = withCandidateAttemptLock(alice, "attempt", () => gate);
    const send = vi.fn(async () => "sent");
    await expect(
      withCandidateAttemptLock(alice, "attempt", send),
    ).rejects.toThrow("lock busy");
    expect(send).not.toHaveBeenCalled();
    expect(await withCandidateAttemptLock(alice, "another", send)).toBe("sent");
    lockActiveCandidateAccount(bob);
    expect(await withCandidateAttemptLock(bob, "attempt", send)).toBe("sent");
    release();
    await running;
    await expect(
      withCandidateAttemptLock(alice, "attempt", send),
    ).rejects.toThrow("account changed");
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
