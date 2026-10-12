import { useMemo } from "react";

import { allFindings, type Finding, type Severity, type VouchReport } from "../types";
import type { Target } from "./ChangePanel";

interface Props {
  report: VouchReport;
  target: Target | null;
  onSelect: (target: Target | null) => void;
}

const SEVERITY_ORDER: Record<Severity, number> = { error: 0, warn: 1, info: 2 };

export function FindingsPanel({ report, target, onSelect }: Props) {
  const groups = useMemo(() => groupByCode(allFindings(report)), [report]);
  // `request-unfulfilled` legitimately points at a file the diff never touched,
  // so "has a file" is not the same as "can be jumped to".
  const changedFiles = useMemo(
    () => new Set(report.changes.map((hunk) => hunk.file)),
    [report],
  );

  if (groups.length === 0) {
    return (
      <p className="empty">
        Nothing flagged. The diff looks like the task — which is not the same as correct.
      </p>
    );
  }

  return (
    <div className="findings">
      {groups.map(([code, findings]) => (
        <section key={code} className="finding-group">
          <h3 className="code-name">
            <span className={`pill ${findings[0]!.severity}`}>{findings[0]!.severity}</span>
            <span className="mono">{code}</span>
            <span className="dim">×{findings.length}</span>
          </h3>

          <ul>
            {findings.map((finding, index) => {
              const locatable = Boolean(finding.file) && changedFiles.has(finding.file!);
              const active =
                locatable && target?.file === finding.file && target?.line === finding.line;
              return (
                <li key={`${code}-${index}`}>
                  <button
                    type="button"
                    className={`finding${active ? " active" : ""}${
                      locatable ? "" : " unlocatable"
                    }`}
                    onClick={() =>
                      locatable
                        ? onSelect(
                            active
                              ? null
                              : { file: finding.file!, ...(finding.line !== undefined && {
                                  line: finding.line,
                                }) },
                          )
                        : undefined
                    }
                    disabled={!locatable}
                  >
                    <span className="finding-message">{finding.message}</span>
                    <span className="finding-meta mono">
                      {finding.file ?? "whole diff"}
                      {finding.line !== undefined && `:${finding.line}`}
                      {finding.file && !locatable && " · not in this diff"}
                      {finding.confidence === "low" && " · low confidence"}
                      {finding.check === "agent" && " · pass 2"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

function groupByCode(findings: Finding[]): [string, Finding[]][] {
  const byCode = new Map<string, Finding[]>();
  for (const finding of findings) {
    const existing = byCode.get(finding.code);
    if (existing) existing.push(finding);
    else byCode.set(finding.code, [finding]);
  }
  return [...byCode.entries()].sort(
    ([, a], [, b]) => SEVERITY_ORDER[a[0]!.severity] - SEVERITY_ORDER[b[0]!.severity],
  );
}
