"""The agentic loop: budget, validation, retry, degradation, cost."""

from __future__ import annotations

from pathlib import Path

from conftest import make_context
from fakes import FakeClient, Response, TextBlock, Usage, text_response, tool_response
from vouch.agent.loop import AgentConfig, run_agent, run_loop
from vouch.agent.prompts import (
    DEFAULT_AGENTIC_CODES,
    build_system_prompt,
    hunk_range_label,
)

SOURCES = {"src/app.py": "def one():\n    return 1\n"}


def context():
    return make_context(task="make one() return 1", sources=SOURCES)


def verdict_for(ctx, classification: str = "requested", **extra):
    payload = {
        "hunks": [
            {
                "file": hunk.file,
                "range": hunk_range_label(hunk),
                "classification": classification,
                "reason": "because",
            }
            for hunk in ctx.hunks
        ],
        "summary": "looks fine",
    }
    payload.update(extra)
    return payload


class TestHappyPath:
    def test_classifications_reach_the_report(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx)))
        section = run_agent(ctx, tmp_path, client=client)
        assert section.ran is True
        assert [h.classification for h in section.hunks] == ["requested"]
        assert section.summary == "looks fine"

    def test_fenced_json_is_tolerated(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx), fenced=True))
        assert run_agent(ctx, tmp_path, client=client).ran is True

    def test_prose_before_json_is_tolerated(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx), prose="Here is my verdict:\n"))
        assert len(run_agent(ctx, tmp_path, client=client).hunks) == 1

    def test_cost_is_reported(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx)))
        cost = run_agent(ctx, tmp_path, client=client).cost
        assert cost.input_tokens == 10
        assert cost.output_tokens == 5
        assert cost.tool_calls == 0

    def test_model_override_is_passed_through(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx)))
        run_agent(ctx, tmp_path, client=client, model="claude-test-9")
        assert client.calls[0]["model"] == "claude-test-9"

    def test_default_model_is_opus(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx)))
        run_agent(ctx, tmp_path, client=client)
        assert client.calls[0]["model"] == "claude-opus-5-5"


class TestDerivedFindings:
    def test_unrequested_hunk_becomes_a_finding(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx, "unrequested")))
        section = run_agent(ctx, tmp_path, client=client)
        assert [f.code for f in section.findings] == ["unrequested-change"]

    def test_model_findings_are_forced_to_warn(self, tmp_path: Path):
        ctx = context()
        payload = verdict_for(
            ctx,
            findings=[
                {
                    "code": "dead-integration",
                    "file": "src/app.py",
                    "message": "never called",
                    "confidence": "high",
                }
            ],
        )
        client = FakeClient(text_response(payload))
        section = run_agent(ctx, tmp_path, client=client)
        assert [f.severity for f in section.findings] == ["warn"]
        assert section.findings[0].check == "agent"

    def test_model_emitted_unrequested_change_is_dropped(self, tmp_path: Path):
        ctx = context()
        payload = verdict_for(
            ctx,
            findings=[
                {"code": "unrequested-change", "file": "src/app.py", "message": "creep"}
            ],
        )
        client = FakeClient(text_response(payload))
        # Derivation is the single source of this code; the hunk is
        # 'requested', so nothing should be reported.
        assert run_agent(ctx, tmp_path, client=client).findings == []


class TestToolUse:
    def test_tool_results_are_fed_back(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("def one():\n    return 1\n", encoding="utf-8")
        ctx = context()
        client = FakeClient(
            tool_response("read_file", {"path": "src/app.py"}),
            text_response(verdict_for(ctx)),
        )
        section = run_agent(ctx, tmp_path, client=client)
        assert section.cost.tool_calls == 1
        tool_result = client.calls[1]["messages"][-1]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert "def one():" in tool_result["content"]

    def test_failing_tool_call_does_not_end_the_run(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(
            tool_response("read_file", {"path": "does_not_exist.py"}),
            text_response(verdict_for(ctx)),
        )
        section = run_agent(ctx, tmp_path, client=client)
        assert section.ran is True
        assert "ERROR" in client.calls[1]["messages"][-1]["content"][0]["content"]

    def test_tools_are_offered_while_under_budget(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response(verdict_for(ctx)))
        run_agent(ctx, tmp_path, client=client)
        assert "tools" in client.calls[0]

    def test_multiple_tool_uses_in_one_turn_are_all_executed(self, tmp_path: Path):
        ctx = context()
        many = Response(
            content=[
                tool_response("list_dir", {"path": "."}, "a").content[0],
                tool_response("list_dir", {"path": "."}, "b").content[0],
            ],
            stop_reason="tool_use",
        )
        client = FakeClient(many, text_response(verdict_for(ctx)))
        assert run_agent(ctx, tmp_path, client=client).cost.tool_calls == 2


class TestBudget:
    def test_tools_are_withdrawn_once_the_budget_is_spent(self, tmp_path: Path):
        ctx = context()
        settings = AgentConfig(tool_budget=2)
        client = FakeClient(
            tool_response("list_dir", {"path": "."}),
            tool_response("list_dir", {"path": "."}),
            text_response(verdict_for(ctx)),
        )
        result = run_loop(client, ctx, tmp_path, settings)
        assert result.cost.tool_calls == 2
        # The final request must offer no tools, which is what forces a verdict.
        assert "tools" not in client.calls[-1]
        assert result.structured is True

    def test_budget_exhausted_prompt_is_sent_once(self, tmp_path: Path):
        ctx = context()
        settings = AgentConfig(tool_budget=1)
        client = FakeClient(
            tool_response("list_dir", {"path": "."}),
            text_response(verdict_for(ctx)),
        )
        run_loop(client, ctx, tmp_path, settings)
        prompts = [
            message["content"]
            for call in client.calls
            for message in call["messages"]
            if message["role"] == "user" and isinstance(message["content"], str)
        ]
        assert sum("Tool budget exhausted" in text for text in prompts) == 1

    def test_a_model_that_only_calls_tools_still_terminates(self, tmp_path: Path):
        ctx = context()
        settings = AgentConfig(tool_budget=3)
        script = [tool_response("list_dir", {"path": "."}) for _ in range(3)]
        script.append(text_response(verdict_for(ctx)))
        client = FakeClient(*script)
        assert run_loop(client, ctx, tmp_path, settings).structured is True


class TestValidationAndRetry:
    def test_malformed_json_triggers_one_retry(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(
            text_response("this is not json at all"),
            text_response(verdict_for(ctx)),
        )
        result = run_loop(client, ctx, tmp_path, AgentConfig())
        assert result.structured is True
        assert client.call_count == 2

    def test_retry_prompt_explains_the_problem(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response("nope"), text_response(verdict_for(ctx)))
        run_loop(client, ctx, tmp_path, AgentConfig())
        retry = client.calls[1]["messages"][-1]["content"]
        assert "did not match the required JSON shape" in retry

    def test_invalid_classification_triggers_retry(self, tmp_path: Path):
        ctx = context()
        bad = {
            "hunks": [{"file": "src/app.py", "range": "1-2", "classification": "maybe"}],
            "summary": "x",
        }
        client = FakeClient(text_response(bad), text_response(verdict_for(ctx)))
        assert run_loop(client, ctx, tmp_path, AgentConfig()).structured is True

    def test_unknown_issue_code_triggers_retry(self, tmp_path: Path):
        ctx = context()
        bad = verdict_for(ctx, findings=[{"code": "invented-code", "message": "x"}])
        client = FakeClient(text_response(bad), text_response(verdict_for(ctx)))
        assert run_loop(client, ctx, tmp_path, AgentConfig()).structured is True

    def test_two_failures_degrade_without_crashing(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response("garbage one"), text_response("garbage two"))
        result = run_loop(client, ctx, tmp_path, AgentConfig())
        assert result.structured is False
        assert result.verdict.hunks == []
        assert "garbage two" in result.verdict.summary

    def test_degraded_run_still_reports_cost(self, tmp_path: Path):
        ctx = context()
        client = FakeClient(text_response("no"), text_response("still no"))
        assert run_loop(client, ctx, tmp_path, AgentConfig()).cost.input_tokens == 20

    def test_empty_response_degrades_with_a_message(self, tmp_path: Path):
        ctx = context()
        empty = Response(content=[TextBlock(text="")], usage=Usage())
        client = FakeClient(empty, empty)
        result = run_loop(client, ctx, tmp_path, AgentConfig())
        assert "no parseable verdict" in result.verdict.summary

    def test_classification_only_verdict_is_valid(self, tmp_path: Path):
        # No findings key at all — the field is additive and defaults to empty.
        ctx = context()
        payload = {
            "hunks": [
                {
                    "file": "src/app.py",
                    "range": hunk_range_label(ctx.hunks[0]),
                    "classification": "supporting",
                    "reason": "r",
                }
            ],
            "summary": "ok",
        }
        client = FakeClient(text_response(payload))
        assert run_loop(client, ctx, tmp_path, AgentConfig()).structured is True


class TestFabricationGuard:
    def test_hunks_we_never_sent_are_dropped(self, tmp_path: Path):
        ctx = context()
        payload = verdict_for(ctx)
        payload["hunks"].append(
            {
                "file": "totally/invented.py",
                "range": "1-9",
                "classification": "unrequested",
                "reason": "made up",
            }
        )
        client = FakeClient(text_response(payload))
        section = run_agent(ctx, tmp_path, client=client)
        assert [h.file for h in section.hunks] == ["src/app.py"]

    def test_a_drifted_range_still_matches_on_file(self, tmp_path: Path):
        ctx = context()
        payload = {
            "hunks": [
                {
                    "file": "src/app.py",
                    "range": "999-1000",
                    "classification": "unrequested",
                    "reason": "r",
                }
            ],
            "summary": "s",
        }
        client = FakeClient(text_response(payload))
        section = run_agent(ctx, tmp_path, client=client)
        assert len(section.hunks) == 1


class TestSystemPrompt:
    def test_rubric_defines_all_three_classifications(self):
        prompt = build_system_prompt(())
        for label in ("requested", "supporting", "unrequested"):
            assert f'"{label}"' in prompt

    def test_prompt_states_intent_not_quality(self):
        assert "not code quality" in build_system_prompt(()).lower()

    def test_enabled_codes_get_a_guide_section(self):
        prompt = build_system_prompt(DEFAULT_AGENTIC_CODES)
        for code in DEFAULT_AGENTIC_CODES:
            assert f"### {code}" in prompt

    def test_unrequested_change_is_never_offered_to_the_model(self):
        # It is derived from classifications, so the model must not emit it.
        assert "### unrequested-change" not in build_system_prompt(DEFAULT_AGENTIC_CODES)

    def test_reserved_codes_are_not_offered(self):
        prompt = build_system_prompt(DEFAULT_AGENTIC_CODES)
        assert "### change-narration" not in prompt
        assert "### misleading-claim" not in prompt

    def test_findings_shape_only_appears_when_codes_are_enabled(self):
        assert '"findings"' not in build_system_prompt(())
        assert '"findings"' in build_system_prompt(DEFAULT_AGENTIC_CODES)
