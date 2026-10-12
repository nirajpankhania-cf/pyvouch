"""Reading the task out of an agent transcript.

Both formats are other tools' private files, so every test here doubles as a
statement about what happens when the shape is not what we expect: nothing
may raise, and a prompt we cannot read is simply not offered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vouch.context import transcripts
from vouch.context.transcripts import (
    Prompt,
    encode_claude_dir,
    encode_cursor_dir,
    latest_prompt,
    recent_prompts,
)

REPO = "/Users/me/dev/my_app"


def cursor_record(text: str, role: str = "user") -> dict:
    """The real Cursor CLI shape: a user's words wrapped in <user_query>."""
    body = (
        f"<timestamp>Monday, Oct 12, 2026, 2:29 PM (UTC-4)</timestamp>\n"
        f"<user_query>\n{text}\n</user_query>"
        if role == "user"
        else text
    )
    return {"role": role, "message": {"content": [{"type": "text", "text": body}]}}


def claude_record(text: str, **extra) -> dict:
    """The real Claude Code shape: content is a bare string."""
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "cwd": REPO,
        **extra,
    }


@pytest.fixture
def homes(tmp_path: Path, monkeypatch):
    """Redirect both transcript roots into the sandbox."""
    cursor = tmp_path / "cursor"
    claude = tmp_path / "claude"
    monkeypatch.setattr(transcripts, "CURSOR_ROOT", cursor)
    monkeypatch.setattr(transcripts, "CLAUDE_ROOT", claude)
    return cursor, claude


def write_cursor(homes, records: list[dict], session: str = "sess-1", repo: str = REPO) -> Path:
    cursor, _ = homes
    directory = cursor / encode_cursor_dir(repo) / "agent-transcripts" / session
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def write_claude(homes, records: list[dict], session: str = "claude-1", repo: str = REPO) -> Path:
    _, claude = homes
    directory = claude / encode_claude_dir(repo)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


class TestPathEncoding:
    def test_cursor_drops_the_leading_separator(self):
        assert encode_cursor_dir("/Users/me/dev/app") == "Users-me-dev-app"

    def test_claude_keeps_it(self):
        assert encode_claude_dir("/Users/me/dev/app") == "-Users-me-dev-app"

    def test_dots_and_underscores_become_dashes(self):
        assert encode_cursor_dir("/Users/me/my_app.v2") == "Users-me-my-app-v2"

    def test_relative_paths_are_resolved(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert encode_cursor_dir(".") == encode_cursor_dir(tmp_path)


class TestCursorFormat:
    def test_reads_the_user_query(self, homes):
        write_cursor(homes, [cursor_record("add retry to the http client")])
        assert latest_prompt(REPO) == "add retry to the http client"

    def test_strips_the_timestamp_wrapper(self, homes):
        write_cursor(homes, [cursor_record("do the thing properly")])
        (prompt,) = recent_prompts(REPO)
        assert "timestamp" not in prompt.text
        assert prompt.text == "do the thing properly"

    def test_ignores_assistant_turns(self, homes):
        write_cursor(
            homes,
            [
                cursor_record("add retry to the http client"),
                cursor_record("I have added retry with backoff.", role="assistant"),
            ],
        )
        assert [p.text for p in recent_prompts(REPO)] == ["add retry to the http client"]

    def test_a_user_record_without_the_marker_is_harness_noise(self, homes):
        write_cursor(
            homes,
            [
                {
                    "role": "user",
                    "message": {"content": [{"type": "text", "text": "tool result: ok"}]},
                },
                cursor_record("add retry to the http client"),
            ],
        )
        assert [p.text for p in recent_prompts(REPO)] == ["add retry to the http client"]

    def test_newest_prompt_in_a_file_wins(self, homes):
        write_cursor(
            homes,
            [cursor_record("first real request"), cursor_record("second real request")],
        )
        assert latest_prompt(REPO) == "second real request"

    def test_tool_is_reported(self, homes):
        write_cursor(homes, [cursor_record("add retry to the http client")])
        assert recent_prompts(REPO)[0].tool == "cursor"

    def test_a_flat_transcript_file_is_also_found(self, homes):
        # Older layout: the jsonl sits directly under agent-transcripts/.
        cursor, _ = homes
        directory = cursor / encode_cursor_dir(REPO) / "agent-transcripts"
        directory.mkdir(parents=True)
        (directory / "flat.jsonl").write_text(
            json.dumps(cursor_record("the flat layout task")) + "\n", encoding="utf-8"
        )
        assert latest_prompt(REPO) == "the flat layout task"


class TestClaudeCodeFormat:
    def test_reads_a_string_content(self, homes):
        write_claude(homes, [claude_record("make the parser handle renames")])
        assert latest_prompt(REPO) == "make the parser handle renames"

    def test_reads_block_content(self, homes):
        write_claude(
            homes,
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "make the parser faster"}],
                    },
                }
            ],
        )
        assert latest_prompt(REPO) == "make the parser faster"

    def test_meta_turns_are_skipped(self, homes):
        write_claude(
            homes,
            [
                claude_record("the real request goes here"),
                claude_record("[Your previous response had no output]", isMeta=True),
            ],
        )
        assert latest_prompt(REPO) == "the real request goes here"

    def test_sidechain_turns_are_skipped(self, homes):
        write_claude(
            homes,
            [
                claude_record("the real request goes here"),
                claude_record("a subagent instruction", isSidechain=True),
            ],
        )
        assert latest_prompt(REPO) == "the real request goes here"

    def test_non_human_origin_is_skipped(self, homes):
        write_claude(
            homes,
            [
                claude_record("the real request goes here"),
                claude_record("injected by a hook", origin={"kind": "hook"}),
            ],
        )
        assert latest_prompt(REPO) == "the real request goes here"

    def test_an_explicit_human_origin_is_kept(self, homes):
        write_claude(homes, [claude_record("a typed request", origin={"kind": "human"})])
        assert latest_prompt(REPO) == "a typed request"

    def test_tool_is_reported(self, homes):
        write_claude(homes, [claude_record("make the parser handle renames")])
        assert recent_prompts(REPO)[0].tool == "claude-code"


class TestAcknowledgementFiltering:
    """A reply to the agent is not a statement of intent."""

    @pytest.mark.parametrize(
        "reply", ["yes", "yh", "ok", "go on", "continue", "thanks", "perfect", "y", "LGTM", "Ok."]
    )
    def test_replies_are_not_tasks(self, homes, reply: str):
        write_cursor(homes, [cursor_record("add retry to the http client"), cursor_record(reply)])
        assert latest_prompt(REPO) == "add retry to the http client"

    def test_very_short_messages_are_not_tasks(self, homes):
        write_cursor(homes, [cursor_record("add retry to the http client"), cursor_record("fix")])
        assert latest_prompt(REPO) == "add retry to the http client"

    def test_a_long_message_starting_with_ok_is_a_task(self, homes):
        write_cursor(homes, [cursor_record("ok now add retry to the http client")])
        assert latest_prompt(REPO) == "ok now add retry to the http client"

    def test_a_transcript_of_only_replies_offers_nothing(self, homes):
        write_cursor(homes, [cursor_record("yes"), cursor_record("ok")])
        assert recent_prompts(REPO) == []
        assert latest_prompt(REPO) is None


class TestMultipleSources:
    def test_both_tools_are_read(self, homes):
        write_cursor(homes, [cursor_record("the cursor request")])
        write_claude(homes, [claude_record("the claude request")])
        assert {p.text for p in recent_prompts(REPO)} == {
            "the cursor request",
            "the claude request",
        }

    def test_newer_file_sorts_first(self, homes):
        old = write_cursor(homes, [cursor_record("the older request")], session="old")
        new = write_cursor(homes, [cursor_record("the newer request")], session="new")
        import os

        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        assert recent_prompts(REPO)[0].text == "the newer request"

    def test_identical_prompts_are_deduplicated(self, homes):
        write_cursor(homes, [cursor_record("the same request")], session="a")
        write_cursor(homes, [cursor_record("the same request")], session="b")
        assert len(recent_prompts(REPO)) == 1

    def test_limit_is_honoured(self, homes):
        write_cursor(homes, [cursor_record(f"request number {i}") for i in range(10)])
        assert len(recent_prompts(REPO, limit=3)) == 3

    def test_another_repo_is_not_read(self, homes):
        write_cursor(homes, [cursor_record("a different project's task")], repo="/Users/me/other")
        assert recent_prompts(REPO) == []


class TestNeverRaises:
    """These are other tools' files. Nothing here may break a run."""

    def test_no_transcript_directory(self, homes):
        assert recent_prompts(REPO) == []
        assert latest_prompt(REPO) is None

    def test_invalid_json_lines_are_skipped(self, homes):
        path = write_cursor(homes, [cursor_record("the good request here")])
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        assert latest_prompt(REPO) == "the good request here"

    def test_a_truncated_final_line_is_tolerated(self, homes):
        path = write_cursor(homes, [cursor_record("the good request here")])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"role": "user", "message": {"cont')
        assert latest_prompt(REPO) == "the good request here"

    def test_an_entirely_different_shape_yields_nothing(self, homes):
        write_cursor(homes, [{"totally": "unexpected"}, {"nested": {"deeply": [1, 2]}}])
        assert recent_prompts(REPO) == []

    def test_null_content_is_tolerated(self, homes):
        write_cursor(homes, [{"role": "user", "message": {"content": None}}])
        assert recent_prompts(REPO) == []

    def test_a_json_list_instead_of_an_object(self, homes):
        write_cursor(homes, [])
        cursor, _ = homes
        path = cursor / encode_cursor_dir(REPO) / "agent-transcripts" / "sess-1" / "sess-1.jsonl"
        path.write_text("[1, 2, 3]\n", encoding="utf-8")
        assert recent_prompts(REPO) == []

    def test_an_empty_file(self, homes):
        write_cursor(homes, [])
        assert recent_prompts(REPO) == []

    def test_an_oversized_file_is_skipped_not_read(self, homes, monkeypatch):
        monkeypatch.setattr(transcripts, "_MAX_BYTES", 10)
        write_cursor(homes, [cursor_record("a request that will not be read")])
        assert recent_prompts(REPO) == []

    def test_invalid_utf8_does_not_raise(self, homes):
        path = write_cursor(homes, [cursor_record("the good request here")])
        with path.open("ab") as handle:
            handle.write(b"\xff\xfe not utf-8\n")
        assert latest_prompt(REPO) == "the good request here"


class TestPromptSummary:
    def test_collapses_whitespace(self):
        prompt = Prompt(text="add   retry\n\nto the client", tool="cursor", session="s", modified=0)
        assert prompt.summary == "add retry to the client"

    def test_truncates_long_text(self):
        prompt = Prompt(text="x" * 400, tool="cursor", session="s", modified=0)
        assert len(prompt.summary) == 118
        assert prompt.summary.endswith("…")

    def test_short_text_is_untouched(self):
        prompt = Prompt(text="add retry", tool="cursor", session="s", modified=0)
        assert prompt.summary == "add retry"
