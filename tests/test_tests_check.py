"""Test-tampering detection: deletions, skip markers, assertion loss."""

from __future__ import annotations

from conftest import make_context, whole_file_hunk
from vouch.checks import tests as tests_check
from vouch.core.types import Hunk, HunkLine


def run_on(file: str, source: str):
    return tests_check.run(make_context(sources={file: source}))


def diff_hunk(file: str, removed: list[str], added: list[str], status: str = "modified") -> Hunk:
    lines = [HunkLine(kind="del", text=t, old_line=i) for i, t in enumerate(removed, 1)]
    lines += [HunkLine(kind="add", text=t, new_line=i) for i, t in enumerate(added, 1)]
    return Hunk(
        file=file,
        status=status,
        old_start=1,
        old_lines=len(removed),
        new_start=1,
        new_lines=len(added),
        lines=lines,
    )


class TestFileRecognition:
    def test_test_prefix_file_is_a_test(self):
        assert len(run_on("test_app.py", "@pytest.mark.skip\n")) == 1

    def test_test_suffix_file_is_a_test(self):
        assert len(run_on("app_test.py", "@pytest.mark.skip\n")) == 1

    def test_tests_directory_is_a_test(self):
        assert len(run_on("tests/anything.py", "@pytest.mark.skip\n")) == 1

    def test_conftest_is_a_test(self):
        assert len(run_on("conftest.py", "@pytest.mark.skip\n")) == 1

    def test_production_file_is_not_a_test(self):
        assert run_on("src/app.py", "@pytest.mark.skip\n") == []


class TestDeletion:
    def test_deleted_test_file_is_an_error(self):
        hunk = Hunk(
            file="tests/test_app.py",
            status="deleted",
            old_start=1,
            old_lines=2,
            new_start=0,
            new_lines=0,
            lines=[HunkLine(kind="del", text="assert True", old_line=1)],
        )
        (finding,) = tests_check.run(make_context(hunks=[hunk]))
        assert finding.severity == "error"
        assert finding.message == "test file deleted"

    def test_deletion_reported_once_not_per_hunk(self):
        hunk = Hunk(
            file="tests/test_app.py",
            status="deleted",
            old_start=1,
            old_lines=1,
            new_start=0,
            new_lines=0,
            lines=[HunkLine(kind="del", text="assert a == 1", old_line=1)],
        )
        findings = tests_check.run(make_context(hunks=[hunk, hunk]))
        assert len(findings) == 1

    def test_deletion_does_not_also_report_assertion_loss(self):
        # The deletion is the finding; counting its assertions double-reports.
        hunk = Hunk(
            file="tests/test_app.py",
            status="deleted",
            old_start=1,
            old_lines=3,
            new_start=0,
            new_lines=0,
            lines=[
                HunkLine(kind="del", text="    assert a == 1", old_line=1),
                HunkLine(kind="del", text="    assert b == 2", old_line=2),
            ],
        )
        findings = tests_check.run(make_context(hunks=[hunk]))
        assert len(findings) == 1

    def test_deleting_a_production_file_is_not_tampering(self):
        hunk = Hunk(
            file="src/app.py",
            status="deleted",
            old_start=1,
            old_lines=1,
            new_start=0,
            new_lines=0,
            lines=[],
        )
        assert tests_check.run(make_context(hunks=[hunk])) == []


class TestSkipMarkers:
    def test_pytest_skip_marker_flags(self):
        (finding,) = run_on("tests/test_a.py", "@pytest.mark.skip(reason='flaky')\n")
        assert finding.severity == "warn"
        assert finding.confidence == "high"

    def test_skipif_is_reported_as_conditional(self):
        (finding,) = run_on("tests/test_a.py", "@pytest.mark.skipif(SLOW, reason='slow')\n")
        assert "conditionally disabled" in finding.message

    def test_xfail_is_reported_as_expected_failure(self):
        (finding,) = run_on("tests/test_a.py", "@pytest.mark.xfail\n")
        assert "expected to fail" in finding.message

    def test_bare_mark_skip_without_pytest_prefix_flags(self):
        assert len(run_on("tests/test_a.py", "@mark.skip\n")) == 1

    def test_imperative_pytest_skip_flags(self):
        assert len(run_on("tests/test_a.py", "    pytest.skip('nope')\n")) == 1

    def test_unittest_skip_decorator_flags(self):
        assert len(run_on("tests/test_a.py", "@unittest.skip('later')\n")) == 1

    def test_self_skiptest_flags(self):
        assert len(run_on("tests/test_a.py", "        self.skipTest('env')\n")) == 1

    def test_removing_a_skip_marker_does_not_flag(self):
        hunk = diff_hunk("tests/test_a.py", removed=["@pytest.mark.skip"], added=["def test_a():"])
        assert tests_check.run(make_context(hunks=[hunk])) == []


class TestAssertionAccounting:
    def test_net_assertion_loss_flags(self):
        hunk = diff_hunk(
            "tests/test_a.py",
            removed=["    assert a == 1", "    assert b == 2", "    assert c == 3"],
            added=["    assert a == 1"],
        )
        (finding,) = tests_check.run(make_context(hunks=[hunk]))
        assert "3 deleted, 1 added" in finding.message
        assert finding.confidence == "medium"

    def test_moved_assertions_stay_silent(self):
        # A refactor that relocates assertions nets to zero.
        hunk = diff_hunk(
            "tests/test_a.py",
            removed=["    assert a == 1", "    assert b == 2"],
            added=["    assert b == 2", "    assert a == 1"],
        )
        assert tests_check.run(make_context(hunks=[hunk])) == []

    def test_added_assertions_never_flag(self):
        hunk = diff_hunk("tests/test_a.py", removed=[], added=["    assert new_thing()"])
        assert tests_check.run(make_context(hunks=[hunk])) == []

    def test_unittest_assertions_are_counted(self):
        hunk = diff_hunk(
            "tests/test_a.py",
            removed=["        self.assertEqual(a, 1)", "        self.assertTrue(b)"],
            added=[],
        )
        assert len(tests_check.run(make_context(hunks=[hunk]))) == 1

    def test_pytest_raises_is_counted_as_an_assertion(self):
        hunk = diff_hunk(
            "tests/test_a.py", removed=["    with pytest.raises(ValueError):"], added=[]
        )
        assert len(tests_check.run(make_context(hunks=[hunk]))) == 1

    def test_assertion_loss_in_production_code_is_ignored(self):
        removed = ["    assert invariant", "    assert other"]
        hunk = diff_hunk("src/app.py", removed=removed, added=[])
        assert tests_check.run(make_context(hunks=[hunk])) == []

    def test_tally_is_per_file(self):
        gutted = diff_hunk("tests/test_a.py", removed=["    assert x", "    assert y"], added=[])
        healthy = diff_hunk("tests/test_b.py", removed=[], added=["    assert z"])
        findings = tests_check.run(make_context(hunks=[gutted, healthy]))
        assert len(findings) == 1
        assert findings[0].file == "tests/test_a.py"


class TestNonCodeFiles:
    def test_markdown_in_a_tests_dir_is_exempt(self):
        assert run_on("tests/README.md", "@pytest.mark.skip\n") == []

    def test_diff_fixture_in_a_tests_dir_is_exempt(self):
        ctx = make_context(
            hunks=[whole_file_hunk("tests/fixtures/x.diff", "+@pytest.mark.skip\n")]
        )
        assert tests_check.run(ctx) == []
