"""Import resolution: missing modules, invented names, and when to stay quiet."""

from __future__ import annotations

from conftest import make_context, whole_file_hunk
from vouch.checks import imports


def run(source: str, file: str = "app.py", **kwargs):
    return imports.run(make_context(sources={file: source}, **kwargs))


class TestThirdPartyResolution:
    def test_installed_package_resolves(self):
        assert run("import os\n", installed={"os"}) == []

    def test_uninstalled_package_is_an_error(self):
        (finding,) = run("import nonexistent_pkg\n", installed={"os"})
        assert finding.code == "unresolved-import"
        assert finding.severity == "error"
        assert "not installed" in finding.message

    def test_submodule_resolves_via_top_level(self):
        assert run("import os.path\n", installed={"os"}) == []

    def test_from_import_of_installed_package_resolves(self):
        assert run("from os import getcwd\n", installed={"os"}) == []

    def test_declines_to_judge_without_a_discovered_environment(self):
        # Without the target repo's env we cannot tell a hallucinated package
        # from a real dependency, so we say nothing.
        assert run("import requests\n", installed=set(), env_discovered=False) == []

    def test_flags_third_party_only_when_env_was_discovered(self):
        assert len(run("import requests\n", installed={"os"}, env_discovered=True)) == 1


class TestGuardedImports:
    def test_try_except_import_error_is_exempt(self):
        source = "try:\n    import optional_dep\nexcept ImportError:\n    optional_dep = None\n"
        assert run(source, installed={"os"}) == []

    def test_module_not_found_error_is_exempt(self):
        source = "try:\n    import optional_dep\nexcept ModuleNotFoundError:\n    pass\n"
        assert run(source, installed={"os"}) == []

    def test_tuple_of_handled_errors_is_exempt(self):
        source = (
            "try:\n    import optional_dep\nexcept (ValueError, ImportError):\n    pass\n"
        )
        assert run(source, installed={"os"}) == []

    def test_bare_except_is_exempt(self):
        source = "try:\n    import optional_dep\nexcept:\n    pass\n"
        assert run(source, installed={"os"}) == []

    def test_try_catching_something_unrelated_still_flags(self):
        source = "try:\n    import ghost_pkg\nexcept ValueError:\n    pass\n"
        assert len(run(source, installed={"os"})) == 1

    def test_import_in_the_except_branch_still_flags(self):
        source = "try:\n    import os\nexcept ImportError:\n    import ghost_pkg\n"
        assert len(run(source, installed={"os"})) == 1


class TestProjectInternalModules:
    def test_existing_internal_module_resolves(self):
        assert (
            run(
                "import mypkg.helpers\n",
                module_files={
                    "mypkg": "src/mypkg/__init__.py",
                    "mypkg.helpers": "src/mypkg/helpers.py",
                },
                module_exports={"mypkg.helpers": {"helper"}},
            )
            == []
        )

    def test_missing_internal_module_is_an_error(self):
        (finding,) = run(
            "import mypkg.ghost\n",
            module_files={"mypkg": "src/mypkg/__init__.py"},
        )
        assert "module not found in this repo" in finding.message

    def test_invented_name_is_an_error(self):
        (finding,) = run(
            "from mypkg.helpers import invented\n",
            module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
            module_exports={"mypkg.helpers": {"real_helper"}},
        )
        assert "has no 'invented'" in finding.message

    def test_real_name_resolves(self):
        assert (
            run(
                "from mypkg.helpers import real_helper\n",
                module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
                module_exports={"mypkg.helpers": {"real_helper"}},
            )
            == []
        )

    def test_submodule_import_is_not_an_invented_name(self):
        # "from pkg import sub" is valid when sub is a module, even though it
        # isn't a name defined in pkg/__init__.py.
        assert (
            run(
                "from mypkg import helpers\n",
                module_files={
                    "mypkg": "src/mypkg/__init__.py",
                    "mypkg.helpers": "src/mypkg/helpers.py",
                },
                module_exports={"mypkg": set()},
            )
            == []
        )

    def test_opaque_module_never_reports_invented_names(self):
        # A star import or __getattr__ means absence proves nothing.
        assert (
            run(
                "from mypkg.helpers import anything\n",
                module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
                module_exports={"mypkg.helpers": {"real"}},
                opaque_modules={"mypkg.helpers"},
            )
            == []
        )

    def test_namespace_package_prefix_resolves(self):
        assert (
            run(
                "import mypkg.sub\n",
                module_files={"mypkg.sub.thing": "mypkg/sub/thing.py"},
            )
            == []
        )

    def test_star_import_is_not_an_invented_name(self):
        assert (
            run(
                "from mypkg.helpers import *\n",
                module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
                module_exports={"mypkg.helpers": {"real"}},
            )
            == []
        )


class TestRelativeImports:
    def test_sibling_module_resolves(self):
        assert (
            run(
                "from .helpers import helper\n",
                file="src/mypkg/app.py",
                module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
                module_exports={"mypkg.helpers": {"helper"}},
            )
            == []
        )

    def test_missing_sibling_is_an_error(self):
        (finding,) = run(
            "from .ghost import thing\n",
            file="src/mypkg/app.py",
            module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
        )
        assert "doesn't resolve to a module in this repo" in finding.message

    def test_invented_name_in_a_sibling_is_an_error(self):
        (finding,) = run(
            "from .helpers import invented\n",
            file="src/mypkg/app.py",
            module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
            module_exports={"mypkg.helpers": {"helper"}},
        )
        assert "has no 'invented'" in finding.message

    def test_parent_package_import_resolves(self):
        assert (
            run(
                "from ..shared import thing\n",
                file="src/mypkg/sub/app.py",
                module_files={"mypkg.shared": "src/mypkg/shared.py"},
                module_exports={"mypkg.shared": {"thing"}},
            )
            == []
        )

    def test_relative_import_is_checked_without_any_environment(self):
        # Filesystem truth doesn't depend on a venv existing.
        (finding,) = run(
            "from .ghost import thing\n",
            file="src/mypkg/app.py",
            module_files={"mypkg.app": "src/mypkg/app.py"},
            env_discovered=False,
        )
        assert finding.severity == "error"


class TestSiblingModules:
    def test_sibling_module_resolves(self):
        # pytest puts a test directory on sys.path, so "from conftest import x"
        # inside tests/ is valid even though tests/ is not a package.
        ctx = make_context(
            sources={"tests/test_a.py": "from conftest import helper\n"},
            installed={"os"},
        )
        ctx.project.sibling_modules = {"tests": {"conftest", "test_a"}}
        assert imports.run(ctx) == []

    def test_sibling_in_a_different_directory_does_not_resolve(self):
        ctx = make_context(
            sources={"src/app.py": "from conftest import helper\n"},
            installed={"os"},
        )
        ctx.project.sibling_modules = {"tests": {"conftest"}}
        assert len(imports.run(ctx)) == 1

    def test_root_level_sibling_resolves(self):
        ctx = make_context(sources={"script.py": "import helper\n"}, installed={"os"})
        ctx.project.sibling_modules = {"": {"helper", "script"}}
        assert imports.run(ctx) == []


class TestScopeOfTheCheck:
    def test_only_added_imports_are_judged(self):
        source = "import ghost_pkg\nimport os\n"
        hunk = whole_file_hunk("app.py", source)
        # Mark the first line as pre-existing context.
        hunk.lines[0].kind = "context"
        hunk.lines[0].old_line = 1
        ctx = make_context(hunks=[hunk], sources={"app.py": source}, installed={"os"})
        assert imports.run(ctx) == []

    def test_non_python_files_are_skipped(self):
        assert run("import ghost_pkg\n", file="notes.txt", installed={"os"}) == []

    def test_unparseable_file_is_skipped(self):
        assert run("import (((\n", installed={"os"}) == []

    def test_file_absent_from_project_is_skipped(self):
        ctx = make_context(hunks=[whole_file_hunk("gone.py", "import ghost\n")], sources={})
        assert imports.run(ctx) == []

    def test_line_number_points_at_the_import(self):
        (finding,) = run("import os\n\n\nimport ghost_pkg\n", installed={"os"})
        assert finding.line == 4

    def test_multiple_bad_imports_on_one_statement(self):
        findings = run(
            "from mypkg.helpers import a, b\n",
            module_files={"mypkg.helpers": "src/mypkg/helpers.py"},
            module_exports={"mypkg.helpers": set()},
        )
        assert len(findings) == 2
