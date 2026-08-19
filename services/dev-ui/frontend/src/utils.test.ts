import { describe, expect, it } from "vitest";
import { asErrorMessage, formatList, parseJsonEditor, pretty, statusTone } from "./utils";

describe("utils", () => {
  it("pretty prints stable JSON for editor payloads", () => {
    expect(pretty({ b: [1, 2], a: true })).toBe('{\n  "b": [\n    1,\n    2\n  ],\n  "a": true\n}');
  });

  it("parses JSON editor input and returns fallback for blank input", () => {
    const fallback = { unchanged: true };

    expect(parseJsonEditor('{"updated":true}', fallback)).toEqual({ updated: true });
    expect(parseJsonEditor("   ", fallback)).toBe(fallback);
  });

  it("normalizes thrown values into user-facing messages", () => {
    expect(asErrorMessage(new Error("Bad request"))).toBe("Bad request");
    expect(asErrorMessage("bad")).toBe("Request failed.");
    expect(asErrorMessage("bad", "Fallback message")).toBe("Fallback message");
  });

  it("formats optional lists for compact table cells", () => {
    expect(formatList(["read", "write", 3])).toBe("read, write, 3");
    expect(formatList([])).toBe("-");
    expect(formatList(undefined)).toBe("-");
  });

  it.each([
    ["ok", "good"],
    ["COMPLETED", "good"],
    ["running", "warn"],
    ["started", "warn"],
    ["failed", "bad"],
    ["cancelled", "bad"],
    ["unknown", "neutral"],
    [undefined, "neutral"]
  ])("maps %s status to %s tone", (status: string | undefined, tone: string) => {
    expect(statusTone(status)).toBe(tone);
  });
});
