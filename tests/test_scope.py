"""Scope-drift heuristic. Always info/low — Pass 2 owns real intent judgment."""

from __future__ import annotations

from conftest import make_context, whole_file_hunk
from vouch.checks import scope


def run(task: str, sources: dict[str, str]):
    return scope.run(make_context(task=task, sources=sources))


def flagged(task: str, sources: dict[str, str]) -> set[str]:
    return {finding.file for finding in run(task, sources)}


class TestSeverityContract:
    def test_findings_are_always_info_and_low(self):
        (finding,) = run("rename the css variables", {"db/migrations/001.py": "ALTER TABLE\n"})
        assert finding.severity == "info"
        assert finding.confidence == "low"
        assert finding.code == "scope-drift"


class TestOverlap:
    def test_unrelated_file_is_flagged(self):
        assert flagged(
            "add retry logic to the http client",
            {"db/migrations/001_add_index.py": "CREATE INDEX\n"},
        ) == {"db/migrations/001_add_index.py"}

    def test_path_token_overlap_rescues_a_file(self):
        task = "add retry logic to the http client"
        assert flagged(task, {"src/http/client.py": "x = 1\n"}) == set()

    def test_identifier_overlap_rescues_a_file(self):
        # The path shares nothing, but an added line names the concept.
        assert flagged(
            "add retry logic to the http client",
            {"src/net/transport.py": "def with_retry(fn):\n    return fn\n"},
        ) == set()

    def test_camel_case_is_split_into_tokens(self):
        assert flagged("add retry logic", {"src/a.py": "class RetryPolicy:\n    pass\n"}) == set()

    def test_snake_case_is_split_into_tokens(self):
        assert flagged("add retry logic", {"src/a.py": "max_retry_count = 3\n"}) == set()

    def test_plural_matches_singular(self):
        assert flagged("rewrite the parser checks", {"src/parsers.py": "x = 1\n"}) == set()

    def test_singular_matches_plural(self):
        assert flagged("rewrite the json parsers", {"src/parser.py": "x = 1\n"}) == set()

    def test_python_keywords_are_not_scope_signal(self):
        # 'import' and 'self' appear in nearly every Python file; treating
        # them as signal would rescue everything.
        assert "import" not in scope.tokenize("fix the imports check")
        assert "self" not in scope.tokenize("use self consistently")


class TestRefusalToJudge:
    def test_task_with_no_signal_judges_nothing(self):
        # "fix it" carries no scope signal; refusing beats guessing.
        assert run("fix it", {"anything/at/all.py": "x = 1\n"}) == []

    def test_task_of_only_stopwords_judges_nothing(self):
        assert run("fix the bug now", {"unrelated/thing.py": "x = 1\n"}) == []

    def test_short_tokens_are_not_signal(self):
        assert run("do ok go", {"unrelated/thing.py": "x = 1\n"}) == []


class TestInfrastructureExemptions:
    def test_pyproject_rides_along(self):
        assert flagged("add retry logic to the client", {"pyproject.toml": "x = 1\n"}) == set()

    def test_lock_files_ride_along(self):
        assert flagged("add retry logic", {"uv.lock": "x\n", "poetry.lock": "y\n"}) == set()

    def test_requirements_ride_along(self):
        assert flagged("add retry logic", {"requirements-dev.txt": "httpx\n"}) == set()

    def test_readme_rides_along(self):
        assert flagged("add retry logic", {"README.md": "docs\n"}) == set()

    def test_github_workflows_ride_along(self):
        assert flagged("add retry logic", {".github/workflows/ci.yml": "on: push\n"}) == set()

    def test_dockerfile_rides_along(self):
        assert flagged("add retry logic", {"Dockerfile": "FROM python\n"}) == set()

    def test_gitignore_rides_along(self):
        assert flagged("add retry logic", {".gitignore": "*.pyc\n"}) == set()

    def test_a_real_source_file_is_still_judged(self):
        assert flagged("add retry logic", {"src/unrelated/parser.py": "x = 1\n"}) != set()


class TestEvidenceAccumulation:
    def test_evidence_accumulates_across_hunks_of_one_file(self):
        # The first hunk shares nothing; the second names the concept, which
        # should rescue the whole file.
        first = whole_file_hunk("src/a.py", "x = 1\n")
        second = whole_file_hunk("src/a.py", "def retry():\n    pass\n")
        ctx = make_context(task="add retry logic", hunks=[first, second])
        assert scope.run(ctx) == []

    def test_old_path_of_a_rename_counts_as_evidence(self):
        hunk = whole_file_hunk("src/b.py", "x = 1\n", status="renamed")
        hunk.old_file = "src/retry_helper.py"
        ctx = make_context(task="move the retry helper", hunks=[hunk])
        assert scope.run(ctx) == []

    def test_each_file_reported_once(self):
        first = whole_file_hunk("src/other.py", "a = 1\n")
        second = whole_file_hunk("src/other.py", "b = 2\n")
        ctx = make_context(task="add retry logic to the client", hunks=[first, second])
        assert len(scope.run(ctx)) == 1


class TestTokenize:
    def test_drops_stopwords_and_short_tokens(self):
        assert scope.tokenize("add the new xy client") == {"client"}

    def test_splits_camel_case(self):
        assert "retry" in scope.tokenize("RetryPolicy")

    def test_lowercases(self):
        assert scope.tokenize("CLIENT") == {"client"}
