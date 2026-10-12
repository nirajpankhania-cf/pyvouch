import { useState } from "react";

import type { DiffModeName, Health, PromptItem, ReviewRequest } from "../types";

interface Props {
  busy: boolean;
  health: Health | null;
  prompts: PromptItem[];
  onSubmit: (body: ReviewRequest) => void;
}

const MODES: { value: DiffModeName; label: string; hint: string }[] = [
  { value: "working", label: "Working tree", hint: "Uncommitted changes, including new files." },
  { value: "staged", label: "Staged", hint: "Only what is in the index." },
  { value: "base", label: "Against a ref", hint: "Merge-base diff, like a pull request." },
];

export function RequestForm({ busy, health, prompts, onSubmit }: Props) {
  const [task, setTask] = useState("");
  const [mode, setMode] = useState<DiffModeName>("working");
  const [baseRef, setBaseRef] = useState("main");
  const [noAgent, setNoAgent] = useState(false);
  const [picking, setPicking] = useState(false);

  const keyMissing = health !== null && !health.agentAvailable;
  const ready = task.trim().length > 0 && !busy && (mode !== "base" || baseRef.trim().length > 0);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready) return;
    onSubmit({
      task: task.trim(),
      mode,
      ...(mode === "base" ? { baseRef: baseRef.trim() } : {}),
      noAgent,
    });
  }

  return (
    <form className="request" onSubmit={submit}>
      <label className="field">
        <span className="field-label">What did you ask for?</span>
        <textarea
          value={task}
          onChange={(event) => setTask(event.target.value)}
          placeholder="e.g. add retry with backoff to the HTTP client"
          rows={4}
          disabled={busy}
        />
      </label>

      {prompts.length > 0 && (
        <div className="field">
          <button
            type="button"
            className="ghost wide"
            onClick={() => setPicking(!picking)}
            disabled={busy}
          >
            {picking ? "Hide" : `Use something you asked an agent (${prompts.length})`}
          </button>

          {picking && (
            <ul className="prompt-list">
              {prompts.map((prompt, index) => (
                <li key={`${prompt.session}-${index}`}>
                  <button
                    type="button"
                    className="prompt-item"
                    onClick={() => {
                      setTask(prompt.text);
                      setPicking(false);
                    }}
                  >
                    <span className="prompt-text">{prompt.summary}</span>
                    <span className="prompt-meta mono">{prompt.tool}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {picking && (
            <span className="hint">
              Newest first. Picked, not applied — vouch never judges a task you did not
              choose.
            </span>
          )}
        </div>
      )}

      <label className="field">
        <span className="field-label">Diff</span>
        <select
          value={mode}
          onChange={(event) => setMode(event.target.value as DiffModeName)}
          disabled={busy}
        >
          {MODES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <span className="hint">{MODES.find((option) => option.value === mode)?.hint}</span>
      </label>

      {mode === "base" && (
        <label className="field">
          <span className="field-label">Base ref</span>
          <input
            value={baseRef}
            onChange={(event) => setBaseRef(event.target.value)}
            placeholder="main"
            disabled={busy}
          />
        </label>
      )}

      <label className="checkbox">
        <input
          type="checkbox"
          checked={noAgent}
          onChange={(event) => setNoAgent(event.target.checked)}
          disabled={busy}
        />
        <span>Pass 1 only (no model, no cost)</span>
      </label>

      {keyMissing && !noAgent && (
        <p className="hint warn-text">
          The server has no API key, so Pass 2 will be skipped either way.
        </p>
      )}

      <button type="submit" className="primary" disabled={!ready}>
        {busy ? "Reviewing…" : "Review"}
      </button>

      {health?.repoRoot && <p className="hint mono">{health.repoRoot}</p>}
    </form>
  );
}
