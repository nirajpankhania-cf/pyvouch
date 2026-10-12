import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";
import { ChangePanel, type Target } from "./components/ChangePanel";
import { FindingsPanel } from "./components/FindingsPanel";
import { HistoryList } from "./components/HistoryList";
import { RequestForm } from "./components/RequestForm";
import { VerdictHeader } from "./components/VerdictHeader";
import type {
  AgentStatus,
  Health,
  PromptItem,
  ReviewRequest,
  RunSummary,
  VouchReport,
} from "./types";

interface Opened {
  id: string | null;
  report: VouchReport;
  agentStatus: AgentStatus | null;
  agentError: string | null;
  durationMs: number | undefined;
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [prompts, setPrompts] = useState<PromptItem[]>([]);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshHistory = useCallback(async () => {
    try {
      const { runs: listed, total: count } = await api.listReviews();
      setRuns(listed);
      setTotal(count);
    } catch (cause) {
      setError(messageOf(cause));
    }
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch((cause) => setError(messageOf(cause)));
    // Transcripts are a convenience; failing to read them is not an error
    // worth showing, since you can always type the task.
    api
      .listPrompts()
      .then((response) => setPrompts(response.prompts))
      .catch(() => setPrompts([]));
    void refreshHistory();
  }, [refreshHistory]);

  async function submit(body: ReviewRequest) {
    setBusy(true);
    setError(null);
    try {
      const response = await api.createReview(body);
      setOpened({
        id: response.id,
        report: response.report,
        agentStatus: response.agentStatus,
        agentError: response.agentError,
        durationMs: response.durationMs,
      });
      setTarget(null);
      await refreshHistory();
    } catch (cause) {
      setError(messageOf(cause));
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setError(null);
    try {
      const report = await api.getReview(id);
      // A stored report has no agentStatus; `ran` is what survives persistence.
      setOpened({
        id,
        report,
        agentStatus: null,
        agentError: null,
        durationMs: runs.find((run) => run.id === id)?.durationMs,
      });
      setTarget(null);
    } catch (cause) {
      setError(messageOf(cause));
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1 className="brand">
          vouch
          {health && <span className="version mono">v{health.version}</span>}
        </h1>
        <p className="tagline">
          You state the task. It says whether the diff matches — not whether it is good code.
        </p>

        <RequestForm busy={busy} health={health} prompts={prompts} onSubmit={submit} />

        <h2 className="section-title">History</h2>
        <HistoryList
          runs={runs}
          total={total}
          selectedId={opened?.id ?? null}
          onOpen={open}
        />
      </aside>

      <main className="main">
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}

        {!opened && !error && (
          <div className="placeholder">
            <p>Describe what you asked for, then request a review.</p>
            <p className="hint">
              Pass 1 is static and free. Pass 2 asks a model to classify every hunk against
              your task.
            </p>
          </div>
        )}

        {opened && (
          <>
            <VerdictHeader
              report={opened.report}
              agentStatus={opened.agentStatus}
              agentError={opened.agentError}
              durationMs={opened.durationMs}
            />
            <div className="panels">
              <section className="panel changes-panel">
                <h2 className="section-title">
                  Changes
                  <span className="dim">
                    {" "}
                    {opened.report.changes.length} hunk
                    {opened.report.changes.length === 1 ? "" : "s"}
                  </span>
                </h2>
                <ChangePanel report={opened.report} target={target} />
              </section>
              <section className="panel findings-panel">
                <h2 className="section-title">Findings</h2>
                <FindingsPanel report={opened.report} target={target} onSelect={setTarget} />
              </section>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function messageOf(cause: unknown): string {
  if (cause instanceof ApiError) return cause.message;
  return cause instanceof Error ? cause.message : String(cause);
}
