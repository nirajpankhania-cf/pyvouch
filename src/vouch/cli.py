"""Typer entry point. Exit codes are API: 0 clean · 1 findings · 2 tool error."""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import typer

from vouch import __version__
from vouch.context.diff import DiffError, DiffMode
from vouch.context.gitcmd import GitError
from vouch.context.task import TaskError
from vouch.core.pipeline import CheckCrashError, PipelineOptions, run_pipeline
from vouch.core.report import exit_code_for
from vouch.core.types import ALL_ISSUE_CODES, CODE_MEANINGS, DETERMINISTIC_ISSUE_CODES
from vouch.envfile import load_env_file
from vouch.render import render
from vouch.store.runs import RunStore

app = typer.Typer(
    name="vouch",
    help="Intent-aware verification for AI-written Python.",
    no_args_is_help=True,
    add_completion=False,
)

EXIT_TOOL_ERROR = 2


@app.command()
def check(
    message: str = typer.Option(
        None, "-m", "--message", help="The task you gave the agent (highest priority)."
    ),
    staged: bool = typer.Option(False, "--staged", help="Check staged changes only."),
    base: str = typer.Option(
        None, "--base", help="Diff against the merge-base with this ref (PR-style)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable report."),
    no_agent: bool = typer.Option(False, "--no-agent", help="Pass 1 only, no API key needed."),
    model: str = typer.Option(None, "--model", help="Override the agent model."),
    save: bool = typer.Option(
        False, "--save", help="Record this run so the workspace UI can open it."
    ),
) -> None:
    """Verify that a diff matches the task you asked for."""
    load_env_file()
    if staged and base:
        _fail("--staged and --base are mutually exclusive.")

    mode = DiffMode.staged() if staged else DiffMode.base(base) if base else DiffMode.working()

    try:
        result = run_pipeline(
            PipelineOptions(
                mode=mode,
                message=message,
                agent=not no_agent,
                model=model,
                # A prompt would hang a piped --json run.
                prompt=not json_out,
            )
        )
    except (DiffError, TaskError, GitError, CheckCrashError) as exc:
        _fail(str(exc))

    if save or os.environ.get("VOUCH_DB"):
        try:
            RunStore().save(result.report, result.duration_ms)
        except sqlite3.Error as exc:
            # A broken store must not change the verdict a CI job sees.
            typer.secho(f"vouch: could not record run ({exc})", fg=typer.colors.YELLOW, err=True)

    if json_out:
        typer.echo(json.dumps(result.report.to_json_dict(), indent=2))
    else:
        render(result)

    raise typer.Exit(exit_code_for(result.report.verdict))


@app.command("list-codes")
def list_codes() -> None:
    """Print the stable issue codes and what they mean."""
    for code in ALL_ISSUE_CODES:
        layer = "deterministic" if code in DETERMINISTIC_ISSUE_CODES else "agentic"
        typer.echo(f"{code:28} {layer:14} {CODE_MEANINGS[code]}")


@app.command()
def version() -> None:
    """Print the vouch version."""
    typer.echo(__version__)


def _fail(message: str) -> None:
    typer.secho(f"vouch: {message}", fg=typer.colors.RED, err=True)
    sys.exit(EXIT_TOOL_ERROR)


if __name__ == "__main__":
    app()
