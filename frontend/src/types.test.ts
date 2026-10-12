import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { allFindings, hunkRange } from "./types";
import type { Finding, FileStatus, Hunk, VouchReport } from "./types";

interface RangeCase {
  name: string;
  status: FileStatus;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  range: string;
}

const contract = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../tests/contract/hunk-ranges.json", import.meta.url)),
    "utf-8",
  ),
) as { cases: RangeCase[] };

function hunkOf(partial: Partial<Hunk>): Hunk {
  return {
    file: "x.py",
    oldFile: null,
    status: "modified",
    oldStart: 1,
    oldLines: 1,
    newStart: 1,
    newLines: 1,
    lines: [],
    ...partial,
  };
}

describe("hunkRange", () => {
  // Shared with tests/test_diff.py. If these drift, the workspace silently
  // stops tagging hunks with their Pass 2 classification.
  it.each(contract.cases)("$name", (testCase) => {
    expect(hunkRange(hunkOf(testCase))).toBe(testCase.range);
  });

  it("covers every file status the schema allows", () => {
    const covered = new Set(contract.cases.map((testCase) => testCase.status));
    expect(covered).toEqual(
      new Set<FileStatus>(["added", "deleted", "modified", "renamed", "binary"]),
    );
  });
});

describe("allFindings", () => {
  function reportOf(deterministic: Finding[], agentFindings?: Finding[]): VouchReport {
    return {
      version: 1,
      task: { text: "t", source: "api" },
      changes: [],
      deterministic,
      agent: agentFindings ? { ran: true, findings: agentFindings } : { ran: false },
      verdict: "review",
      meta: { createdAt: "2026-01-01T00:00:00Z" },
    };
  }

  const finding = (code: string): Finding => ({
    check: "placeholders",
    code,
    severity: "warn",
    message: "m",
    confidence: "high",
  });

  it("returns an empty list for a clean report", () => {
    expect(allFindings(reportOf([]))).toEqual([]);
  });

  it("keeps pass 1 findings when pass 2 did not run", () => {
    expect(allFindings(reportOf([finding("placeholder-code")]))).toHaveLength(1);
  });

  it("concatenates both passes, pass 1 first", () => {
    const combined = allFindings(
      reportOf([finding("placeholder-code")], [finding("unrequested-change")]),
    );
    expect(combined.map((item) => item.code)).toEqual([
      "placeholder-code",
      "unrequested-change",
    ]);
  });

  it("tolerates an agent section with no findings array", () => {
    expect(allFindings(reportOf([], []))).toEqual([]);
  });
});
