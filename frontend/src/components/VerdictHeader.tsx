import { useState } from "react";

import {
  AGENT_NOTICE,
  countsLabel,
  duration,
  estimatedCost,
  shortSha,
  VERDICT_LABEL,
} from "../format";
import { allFindings, type AgentStatus, type Severity, type VouchReport } from "../types";

interface Props {
  report: VouchReport;
  agentStatus: AgentStatus | null;
  agentError: string | null;
  durationMs: number | undefined;
}

export function VerdictHeader({ report, agentStatus, agentError, durationMs }: Props) {
  const [copied, setCopied] = useState(false);
  const counts = tally(report);
  const cost = report.agent.cost;

  async function copyJson() {
    await navigator.clipboard.writeText(JSON.stringify(report, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <header className="verdict-header">
      <div className="verdict-row">
        <span className={`badge ${report.verdict}`}>{VERDICT_LABEL[report.verdict]}</span>
        <span className="counts">{countsLabel(counts)}</span>
        <span className="spacer" />
        <button type="button" className="ghost" onClick={copyJson}>
          {copied ? "Copied" : "Copy JSON"}
        </button>
      </div>

      <p className="task-text">{report.task.text}</p>

      <dl className="facts">
        <Fact label="task from" value={report.task.source} />
        {report.meta.branch && <Fact label="branch" value={report.meta.branch} />}
        {report.meta.commit && <Fact label="commit" value={shortSha(report.meta.commit)} />}
        <Fact label="hunks" value={String(report.changes.length)} />
        {durationMs !== undefined && <Fact label="took" value={duration(durationMs)} />}
        <Fact label="pass 2" value={report.agent.ran ? "ran" : "skipped"} />
        {cost && (
          <Fact
            label="cost"
            value={`${cost.inputTokens.toLocaleString()} in / ${cost.outputTokens.toLocaleString()} out · ${cost.toolCalls} tools · ~${estimatedCost(
              cost.inputTokens,
              cost.outputTokens,
            )}`}
          />
        )}
      </dl>

      {report.agent.summary && <p className="agent-summary">{report.agent.summary}</p>}

      {agentStatus && agentStatus !== "ran" && (
        <p className="notice">
          {AGENT_NOTICE[agentStatus]}
          {agentError && <span className="mono"> {agentError}</span>}
        </p>
      )}
    </header>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function tally(report: VouchReport): Record<Severity, number> {
  const counts: Record<Severity, number> = { error: 0, warn: 0, info: 0 };
  for (const finding of allFindings(report)) counts[finding.severity] += 1;
  return counts;
}
