import { duration, relativeTime } from "../format";
import type { RunSummary } from "../types";

interface Props {
  runs: RunSummary[];
  total: number;
  selectedId: string | null;
  onOpen: (id: string) => void;
}

export function HistoryList({ runs, total, selectedId, onOpen }: Props) {
  if (runs.length === 0) {
    return <p className="empty">No runs yet. Request a review to start.</p>;
  }

  return (
    <>
      <ul className="history">
        {runs.map((run) => (
          <li key={run.id}>
            <button
              type="button"
              className={`history-item${run.id === selectedId ? " selected" : ""}`}
              onClick={() => onOpen(run.id)}
            >
              <span className={`dot ${run.verdict}`} aria-hidden="true" />
              <span className="history-task">{run.taskText}</span>
              <span className="history-meta">
                {relativeTime(run.createdAt)}
                {run.findingCount > 0 && ` · ${run.findingCount} finding${
                  run.findingCount === 1 ? "" : "s"
                }`}
                {run.durationMs !== undefined && ` · ${duration(run.durationMs)}`}
                {!run.agentRan && " · pass 1"}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {total > runs.length && (
        <p className="hint">
          Showing {runs.length} of {total}.
        </p>
      )}
    </>
  );
}
