/**
 * The wire format from `docs/SPEC.md`. Mirrors the pydantic models; the Python
 * side is authoritative, so change these only to follow a SPEC change.
 */

export type Verdict = "clean" | "review" | "fail";
export type Severity = "error" | "warn" | "info";
export type Confidence = "high" | "medium" | "low";
export type Classification = "requested" | "supporting" | "unrequested";
export type TaskSource = "flag" | "taskfile" | "transcript" | "prompt" | "api";
export type DiffModeName = "working" | "staged" | "base";
export type AgentStatus = "ran" | "disabled" | "no-api-key" | "no-hunks" | "error";

export type FileStatus = "added" | "deleted" | "modified" | "renamed" | "binary";
export type LineKind = "add" | "del" | "context";

export interface HunkLine {
  kind: LineKind;
  text: string;
  oldLine: number | null;
  newLine: number | null;
}

export interface Hunk {
  file: string;
  oldFile: string | null;
  status: FileStatus;
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  lines: HunkLine[];
}

export interface Finding {
  check: string;
  code: string;
  severity: Severity;
  message: string;
  confidence: Confidence;
  file?: string;
  line?: number;
}

export interface AgentHunk {
  file: string;
  range: string;
  classification: Classification;
  reason: string;
}

export interface AgentCost {
  inputTokens: number;
  outputTokens: number;
  toolCalls: number;
}

export interface AgentSection {
  ran: boolean;
  hunks?: AgentHunk[];
  findings?: Finding[];
  summary?: string;
  cost?: AgentCost;
}

export interface VouchReport {
  version: number;
  task: { text: string; source: TaskSource };
  changes: Hunk[];
  deterministic: Finding[];
  agent: AgentSection;
  verdict: Verdict;
  meta: {
    createdAt: string;
    commit?: string;
    branch?: string;
    repoRoot?: string;
  };
}

export interface ReviewRequest {
  task: string;
  mode: DiffModeName;
  baseRef?: string;
  noAgent?: boolean;
  model?: string;
}

export interface ReviewResponse {
  id: string;
  report: VouchReport;
  agentStatus: AgentStatus;
  agentError: string | null;
  durationMs: number;
}

export interface RunSummary {
  id: string;
  createdAt: string;
  taskText: string;
  taskSource: TaskSource;
  verdict: Verdict;
  agentRan: boolean;
  findingCount: number;
  errorCount: number;
  branch?: string;
  commit?: string;
  durationMs?: number;
}

export interface ReviewListResponse {
  runs: RunSummary[];
  total: number;
}

export interface PromptItem {
  text: string;
  summary: string;
  tool: string;
  session: string;
}

export interface PromptListResponse {
  prompts: PromptItem[];
}

export interface Health {
  status: string;
  version: string;
  repoRoot?: string;
  agentAvailable: boolean;
}

/**
 * Mirrors Python's `Hunk.range`. This is the key `agent.hunks` join on, so the
 * two definitions must stay identical or classification tags silently vanish.
 */
export function hunkRange(hunk: Hunk): string {
  if (hunk.status === "binary") return "binary";
  if (hunk.status === "deleted") {
    return `${hunk.oldStart}-${hunk.oldStart + Math.max(hunk.oldLines, 1) - 1}`;
  }
  return `${hunk.newStart}-${hunk.newStart + Math.max(hunk.newLines, 1) - 1}`;
}

/** All findings in one list; the API keeps the two passes separate. */
export function allFindings(report: VouchReport): Finding[] {
  return [...report.deterministic, ...(report.agent.findings ?? [])];
}
