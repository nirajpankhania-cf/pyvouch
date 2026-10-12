import { useEffect, useMemo } from "react";

import { CLASSIFICATION_LABEL } from "../format";
import { hunkRange, type AgentHunk, type Classification, type Hunk, type VouchReport } from "../types";

export interface Target {
  file: string;
  line?: number;
}

interface Props {
  report: VouchReport;
  target: Target | null;
}

export function hunkId(file: string, range: string): string {
  return `hunk-${file}-${range}`.replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function ChangePanel({ report, target }: Props) {
  const classifications = useMemo(() => classificationIndex(report.agent.hunks), [report]);
  const files = useMemo(() => groupByFile(report.changes), [report]);
  const focusedId = useMemo(() => focusOf(report.changes, target), [report, target]);

  useEffect(() => {
    if (!focusedId) return;
    document.getElementById(focusedId)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusedId]);

  if (report.changes.length === 0) {
    return <p className="empty">No changes in this diff.</p>;
  }

  return (
    <div className="changes">
      {files.map(([file, hunks]) => (
        <section key={file} className="file">
          <h3 className="file-name">
            <span className={`status ${hunks[0]!.status}`}>{hunks[0]!.status}</span>
            {hunks[0]!.oldFile && <span className="old-name">{hunks[0]!.oldFile} →</span>}
            <span className="mono">{file}</span>
          </h3>

          {hunks.map((hunk) => {
            const range = hunkRange(hunk);
            const id = hunkId(file, range);
            const classification = classifications.get(`${file}@${range}`);
            return (
              <article
                key={range}
                id={id}
                className={`hunk${id === focusedId ? " focused" : ""}`}
              >
                <div className="hunk-head">
                  <span className="mono dim">
                    @@ -{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines} @@
                  </span>
                  {classification && (
                    <span className={`tag ${classification.classification}`}>
                      {CLASSIFICATION_LABEL[classification.classification]}
                    </span>
                  )}
                </div>

                {classification?.reason && <p className="hunk-reason">{classification.reason}</p>}

                {hunk.status === "binary" ? (
                  <p className="hint">Binary file — contents not shown.</p>
                ) : (
                  <table className="diff">
                    <tbody>
                      {hunk.lines.map((line, index) => (
                        <tr key={index} className={line.kind}>
                          <td className="gutter">{line.oldLine ?? ""}</td>
                          <td className="gutter">{line.newLine ?? ""}</td>
                          <td className="sign">
                            {line.kind === "add" ? "+" : line.kind === "del" ? "-" : " "}
                          </td>
                          <td className="code">{line.text || "\u00a0"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </article>
            );
          })}
        </section>
      ))}
    </div>
  );
}

function classificationIndex(
  hunks: AgentHunk[] | undefined,
): Map<string, { classification: Classification; reason: string }> {
  const index = new Map<string, { classification: Classification; reason: string }>();
  for (const hunk of hunks ?? []) {
    index.set(`${hunk.file}@${hunk.range}`, {
      classification: hunk.classification,
      reason: hunk.reason,
    });
  }
  return index;
}

function groupByFile(changes: Hunk[]): [string, Hunk[]][] {
  const byFile = new Map<string, Hunk[]>();
  for (const hunk of changes) {
    const existing = byFile.get(hunk.file);
    if (existing) existing.push(hunk);
    else byFile.set(hunk.file, [hunk]);
  }
  return [...byFile.entries()];
}

/** The hunk a finding points at: same file, and containing the line if given. */
function focusOf(changes: Hunk[], target: Target | null): string | null {
  if (!target) return null;
  const inFile = changes.filter((hunk) => hunk.file === target.file);
  if (inFile.length === 0) return null;
  const match =
    target.line === undefined
      ? inFile[0]!
      : (inFile.find(
          (hunk) =>
            target.line! >= hunk.newStart && target.line! < hunk.newStart + hunk.newLines,
        ) ?? inFile[0]!);
  return hunkId(match.file, hunkRange(match));
}
