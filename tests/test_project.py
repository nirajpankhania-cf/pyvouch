"""Project access and target-environment discovery."""

from __future__ import annotations

import sys
from pathlib import Path

from vouch.context.project import build_project_access
from vouch.core.types import Hunk, HunkLine


def hunk(file: str, status: str = "modified") -> Hunk:
    return Hunk(
        file=file,
        status=status,
        old_start=1,
        old_lines=1,
        new_start=1,
        new_lines=1,
        lines=[HunkLine(kind="add", text="x = 1", new_line=1)],
    )


def make_venv(root: Path, name: str = ".venv") -> Path:
    site = root / name / "lib" / f"python3.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True)
    return site


class TestTouchedFiles:
    def test_reads_post_change_contents(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("value = 42\n", encoding="utf-8")
        access = build_project_access([hunk("a.py")], tmp_path)
        assert access.files["a.py"] == "value = 42\n"

    def test_deleted_files_are_absent(self, tmp_path: Path):
        access = build_project_access([hunk("gone.py", "deleted")], tmp_path)
        assert "gone.py" not in access.files

    def test_binary_files_are_absent(self, tmp_path: Path):
        (tmp_path / "b.bin").write_bytes(b"\x00\x01")
        access = build_project_access([hunk("b.bin", "binary")], tmp_path)
        assert "b.bin" not in access.files

    def test_unreadable_file_is_skipped_not_fatal(self, tmp_path: Path):
        access = build_project_access([hunk("never_written.py")], tmp_path)
        assert access.files == {}

    def test_invalid_utf8_is_skipped(self, tmp_path: Path):
        (tmp_path / "weird.py").write_bytes(b"\xff\xfe invalid")
        access = build_project_access([hunk("weird.py")], tmp_path)
        assert "weird.py" not in access.files

    def test_each_file_read_once_across_hunks(self, tmp_path: Path):
        (tmp_path / "multi.py").write_text("a = 1\n", encoding="utf-8")
        access = build_project_access([hunk("multi.py"), hunk("multi.py")], tmp_path)
        assert list(access.files) == ["multi.py"]


class TestEnvironmentDiscovery:
    def test_no_environment_is_reported_honestly(self, tmp_path: Path):
        access = build_project_access([], tmp_path)
        assert access.env_discovered is False

    def test_in_repo_venv_is_discovered(self, tmp_path: Path):
        site = make_venv(tmp_path)
        (site / "requests").mkdir()
        access = build_project_access([], tmp_path)
        assert access.env_discovered is True
        assert "requests" in access.installed_modules

    def test_does_not_resolve_against_vouchs_own_environment(self, tmp_path: Path):
        # vouch's venv has pydantic installed; a bare target repo must not
        # inherit it, or every real dependency would look hallucinated.
        access = build_project_access([], tmp_path)
        assert "pydantic" not in access.installed_modules

    def test_single_file_modules_count(self, tmp_path: Path):
        site = make_venv(tmp_path)
        (site / "six.py").write_text("", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "six" in access.installed_modules

    def test_extension_modules_count(self, tmp_path: Path):
        site = make_venv(tmp_path)
        (site / "_speedup.cpython-313-darwin.so").write_bytes(b"")
        access = build_project_access([], tmp_path)
        assert "_speedup" in access.installed_modules

    def test_dist_info_metadata_is_ignored_as_a_module(self, tmp_path: Path):
        site = make_venv(tmp_path)
        (site / "requests-2.0.dist-info").mkdir()
        access = build_project_access([], tmp_path)
        assert not any(m.endswith("dist-info") for m in access.installed_modules)

    def test_top_level_txt_maps_dist_to_import_name(self, tmp_path: Path):
        site = make_venv(tmp_path)
        info = site / "PyYAML-6.0.dist-info"
        info.mkdir()
        (info / "top_level.txt").write_text("yaml\n", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "yaml" in access.installed_modules

    def test_editable_install_pth_is_followed(self, tmp_path: Path):
        site = make_venv(tmp_path)
        elsewhere = tmp_path / "other" / "src"
        (elsewhere / "mylib").mkdir(parents=True)
        (elsewhere / "mylib" / "__init__.py").write_text("", encoding="utf-8")
        (site / "__editable__.mylib.pth").write_text(f"{elsewhere}\n", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "mylib" in access.installed_modules

    def test_pth_import_lines_are_not_treated_as_paths(self, tmp_path: Path):
        site = make_venv(tmp_path)
        (site / "weird.pth").write_text("import sys; sys.path.append('x')\n", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert access.env_discovered is True


class TestStdlibAndProjectModules:
    def test_stdlib_is_always_importable(self, tmp_path: Path):
        access = build_project_access([], tmp_path)
        for module in ("os", "sys", "json", "ast", "pathlib"):
            assert module in access.installed_modules

    def test_flat_layout_packages_are_importable(self, tmp_path: Path):
        (tmp_path / "mypkg").mkdir()
        (tmp_path / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "mypkg" in access.installed_modules

    def test_src_layout_packages_are_importable(self, tmp_path: Path):
        (tmp_path / "src" / "mypkg").mkdir(parents=True)
        (tmp_path / "src" / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "mypkg" in access.installed_modules

    def test_top_level_scripts_are_importable(self, tmp_path: Path):
        (tmp_path / "helper.py").write_text("", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "helper" in access.installed_modules

    def test_directory_without_init_is_not_a_package(self, tmp_path: Path):
        (tmp_path / "notapkg").mkdir()
        access = build_project_access([], tmp_path)
        assert "notapkg" not in access.installed_modules


class TestDeclaredDependencyFallback:
    def test_pyproject_dependencies_are_trusted(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "thing"\ndependencies = ["requests>=2", "typer"]\n',
            encoding="utf-8",
        )
        access = build_project_access([], tmp_path)
        assert {"requests", "typer", "thing"} <= access.installed_modules
        assert access.env_discovered is False

    def test_optional_dependencies_count(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "t"\n'
            '[project.optional-dependencies]\ndev = ["pytest", "uvicorn[standard]>=0.3"]\n',
            encoding="utf-8",
        )
        access = build_project_access([], tmp_path)
        assert {"pytest", "uvicorn"} <= access.installed_modules

    def test_requirements_txt_is_read(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text(
            "# comment\nflask==3.0.0\n-e .\n\nhttpx\n", encoding="utf-8"
        )
        access = build_project_access([], tmp_path)
        assert {"flask", "httpx"} <= access.installed_modules

    def test_dist_names_map_to_import_names(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text(
            "PyYAML\nbeautifulsoup4\npython-dateutil\n", encoding="utf-8"
        )
        access = build_project_access([], tmp_path)
        assert {"yaml", "bs4", "dateutil"} <= access.installed_modules

    def test_hyphens_become_underscores(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("my-cool-lib\n", encoding="utf-8")
        access = build_project_access([], tmp_path)
        assert "my_cool_lib" in access.installed_modules

    def test_malformed_pyproject_does_not_raise(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project\nbroken", encoding="utf-8")
        assert build_project_access([], tmp_path).env_discovered is False

    def test_venv_wins_over_declared_dependencies(self, tmp_path: Path):
        make_venv(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "t"\ndependencies = ["never-installed"]\n', encoding="utf-8"
        )
        access = build_project_access([], tmp_path)
        assert access.env_discovered is True
        assert "never_installed" not in access.installed_modules
