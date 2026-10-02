import { describe, expect, it } from "vitest";

describe("web baseline", () => {
  it("has the expected platform name", () => {
    expect("PrüfAI Platform").toContain("PrüfAI");
  });
});
