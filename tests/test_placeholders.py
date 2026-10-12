"""Placeholder detection: comment idioms and AST-verified empty bodies."""

from __future__ import annotations

from conftest import make_context, whole_file_hunk
from vouch.checks import placeholders
from vouch.core.types import Hunk, HunkLine


def run(source: str, file: str = "app.py"):
    return placeholders.run(make_context(sources={file: source}))


class TestCommentMarkers:
    def test_todo_comment_flags(self):
        (finding,) = run("x = 1  # TODO: wire this up\n")
        assert finding.code == "placeholder-code"
        assert finding.severity == "warn"
        assert finding.line == 1

    def test_fixme_flags(self):
        assert len(run("# FIXME broken\n")) == 1

    def test_not_implemented_comment_flags(self):
        (finding,) = run("# not implemented yet\n")
        assert finding.confidence == "high"

    def test_rest_of_implementation_hand_wave_flags(self):
        (finding,) = run("# ... rest of the implementation\n")
        assert "rest of" in finding.message

    def test_goes_here_flags(self):
        assert len(run("# actual logic goes here\n")) == 1

    def test_todo_in_a_string_is_not_a_comment(self):
        # '#' inside a literal doesn't start a comment.
        assert run('label = "TODO: pick a name"\n') == []

    def test_todo_in_an_identifier_does_not_flag(self):
        assert run("todo_items = []\n") == []

    def test_quoted_marker_in_a_comment_is_prose(self):
        # This module's own comments quote these markers while documenting
        # them; discussing an idiom is not declaring a stub.
        assert run('# the "not implemented" marker means a stub\n') == []

    def test_unquoted_marker_after_a_quote_still_flags(self):
        assert len(run('# see "docs" — not implemented\n')) == 1

    def test_comment_after_code_with_hash_in_string(self):
        assert len(run('url = "http://x/#frag"  # TODO fix\n')) == 1

    def test_prose_files_are_exempt(self):
        assert run("- [ ] TODO: write this\n", file="docs/PLAN.md") == []

    def test_diff_fixtures_are_exempt(self):
        assert run("+# TODO something\n", file="tests/fixtures/a.diff") == []


class TestRemovedLinesNeverFlag:
    def test_deleting_a_todo_is_not_a_finding(self):
        hunk = Hunk(
            file="app.py",
            status="modified",
            old_start=1,
            old_lines=1,
            new_start=1,
            new_lines=1,
            lines=[
                HunkLine(kind="del", text="# TODO: old debt", old_line=1),
                HunkLine(kind="add", text="value = compute()", new_line=1),
            ],
        )
        ctx = make_context(hunks=[hunk], sources={"app.py": "value = compute()\n"})
        assert placeholders.run(ctx) == []

    def test_untouched_context_lines_do_not_flag(self):
        hunk = Hunk(
            file="app.py",
            status="modified",
            old_start=1,
            old_lines=2,
            new_start=1,
            new_lines=2,
            lines=[
                HunkLine(kind="context", text="# TODO: pre-existing", old_line=1, new_line=1),
                HunkLine(kind="add", text="x = 1", new_line=2),
            ],
        )
        ctx = make_context(hunks=[hunk], sources={"app.py": "# TODO: pre-existing\nx = 1\n"})
        assert placeholders.run(ctx) == []


class TestEmptyBodies:
    def test_pass_only_function_flags(self):
        (finding,) = run("def handler():\n    pass\n")
        assert "handler" in finding.message
        assert finding.line == 1

    def test_ellipsis_only_function_flags(self):
        (finding,) = run("def handler():\n    ...\n")
        assert "empty body" in finding.message

    def test_docstring_plus_pass_still_flags(self):
        (finding,) = run('def handler():\n    """Does the thing."""\n    pass\n')
        assert "handler" in finding.message

    def test_not_implemented_error_stub_flags(self):
        (finding,) = run("def handler():\n    raise NotImplementedError\n")
        assert "NotImplementedError" in finding.message

    def test_not_implemented_error_with_message_flags(self):
        assert len(run('def handler():\n    raise NotImplementedError("soon")\n')) == 1

    def test_real_implementation_does_not_flag(self):
        assert run("def handler():\n    return 42\n") == []

    def test_docstring_plus_code_does_not_flag(self):
        assert run('def handler():\n    """Doc."""\n    return 1\n') == []

    def test_async_function_flags(self):
        assert len(run("async def handler():\n    pass\n")) == 1

    def test_multi_statement_body_does_not_flag(self):
        assert run("def handler():\n    x = 1\n    return x\n") == []


class TestEmptyBodiesThatAreIdiomatic:
    def test_abstractmethod_is_exempt(self):
        source = (
            "from abc import ABC, abstractmethod\n\n\n"
            "class Base(ABC):\n"
            "    @abstractmethod\n"
            "    def run(self):\n"
            "        ...\n"
        )
        assert run(source) == []

    def test_overload_is_exempt(self):
        source = (
            "from typing import overload\n\n\n"
            "@overload\n"
            "def f(x: int) -> int:\n"
            "    ...\n"
        )
        assert run(source) == []

    def test_protocol_methods_are_exempt(self):
        source = (
            "from typing import Protocol\n\n\n"
            "class Shape(Protocol):\n"
            "    def area(self):\n"
            "        ...\n"
        )
        assert run(source) == []

    def test_abc_subclass_methods_are_exempt(self):
        source = "from abc import ABC\n\n\nclass Base(ABC):\n    def hook(self):\n        pass\n"
        assert run(source) == []

    def test_abcmeta_metaclass_is_exempt(self):
        source = (
            "from abc import ABCMeta\n\n\n"
            "class Base(metaclass=ABCMeta):\n"
            "    def hook(self):\n"
            "        pass\n"
        )
        assert run(source) == []

    def test_pyi_stub_files_are_exempt(self):
        assert run("def f() -> int: ...\n", file="app.pyi") == []

    def test_empty_class_body_is_not_a_function_stub(self):
        assert run("class MyError(Exception):\n    pass\n") == []

    def test_except_pass_is_not_a_stub(self):
        source = "def f():\n    try:\n        risky()\n    except ValueError:\n        pass\n"
        assert run(source) == []

    def test_concrete_method_in_abc_still_flags(self):
        # Inheriting ABC exempts the class, but a plain class next to it does not.
        source = "class Plain:\n    def forgotten(self):\n        pass\n"
        assert len(run(source)) == 1


class TestOnlyAddedDefinitions:
    def test_preexisting_stub_is_not_attributed_to_this_diff(self):
        source = "def old_stub():\n    pass\n\n\ndef added():\n    return 1\n"
        hunk = Hunk(
            file="app.py",
            status="modified",
            old_start=1,
            old_lines=0,
            new_start=5,
            new_lines=2,
            lines=[
                HunkLine(kind="add", text="def added():", new_line=5),
                HunkLine(kind="add", text="    return 1", new_line=6),
            ],
        )
        ctx = make_context(hunks=[hunk], sources={"app.py": source})
        assert placeholders.run(ctx) == []

    def test_unparseable_file_is_skipped_quietly(self):
        # Half-written code is common mid-edit; a syntax error isn't our finding.
        assert run("def broken(\n") == []

    def test_file_absent_from_project_is_skipped(self):
        ctx = make_context(hunks=[whole_file_hunk("gone.py", "def f():\n    pass\n")], sources={})
        assert placeholders.run(ctx) == []
