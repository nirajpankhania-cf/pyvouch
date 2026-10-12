import type { AgentStatus, Classification, Severity, Verdict } from "./types";

export const VERDICT_LABEL: Record<Verdict, string> = {
  clean: "Clean",
  review: "Needs review",
  fail: "Fail",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  error: "error",
  warn: "warn",
  info: "info",
};

export const CLASSIFICATION_LABEL: Record<Classification, string> = {
  requested: "requested",
  supporting: "supporting",
  unrequested: "unrequested",
};

/** Why Pass 2 didn't run. `ran` needs no explanation. */
export const AGENT_NOTICE: Record<Exclude<AgentStatus, "ran">, string> = {
  disabled: "Pass 2 was switched off for this run.",
  "no-api-key": "Pass 2 skipped: the server has no ANTHROPIC_API_KEY.",
  "no-hunks": "Pass 2 skipped: nothing changed.",
  error: "Pass 2 failed; showing Pass 1 only.",
};

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function duration(ms: number | undefined): string {
  if (ms === undefined) return "";
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

export function shortSha(sha: string | undefined): string {
  return sha ? sha.slice(0, 7) : "";
}

/**
 * Anthropic list pricing for Opus, in dollars per million tokens. An estimate
 * shown as "~$" — the report carries token counts, which are the real number.
 */
const INPUT_PER_MTOK = 15;
const OUTPUT_PER_MTOK = 75;

export function estimatedCost(inputTokens: number, outputTokens: number): string {
  const dollars =
    (inputTokens / 1_000_000) * INPUT_PER_MTOK + (outputTokens / 1_000_000) * OUTPUT_PER_MTOK;
  if (dollars === 0) return "$0.00";
  return dollars < 0.01 ? "<$0.01" : `$${dollars.toFixed(2)}`;
}

export function countsLabel(counts: Record<Severity, number>): string {
  const parts: string[] = [];
  if (counts.error) parts.push(`${counts.error} error`);
  if (counts.warn) parts.push(`${counts.warn} warn`);
  if (counts.info) parts.push(`${counts.info} info`);
  return parts.length ? parts.join(", ") : "no findings";
}
