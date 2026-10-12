"""Diff acquisition and parsing.

All git I/O for the diff lives here; checks receive hunks via ``CheckContext``
and never touch git themselves. ``parse_diff_to_hunks`` is pure so it can be
tested against fixture files.

Paths in the result are git-normalized (forward slashes), matching how git
reports them. Conversion to filesystem paths happens at the I/O boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vouch.context.gitcmd import GitError, is_inside_repo, run_git, untracked_files
from vouch.core.types import FileStatus, Hunk, HunkLine


class DiffError(Exception):
    """A diff could not be obtained. The CLI maps this to exit 2."""


@dataclass(frozen=True)
class DiffMode:
    """``working`` (vs HEAD) · ``staged`` · ``base`` (merge-base with ref)."""

    kind: str = "working"
    ref: str | None = None

    @staticmethod
    def working() -> DiffMode:
        return DiffMode("working")

    @staticmethod
    def staged() -> DiffMode:
        return DiffMode("staged")

    @staticmethod
    def base(ref: str) -> DiffMode:
        return DiffMode("base", ref)


_HUNK_HEADER = re.compile(r"^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BINARY_MARKER = re.compile(r"^Binary files (?:a/)?(.+?) and (?:b/)?(.+?) differ$")


def _git_diff_args(mode: DiffMode) -> list[str]:
    base = ["diff", "--no-color", "--no-ext-diff", "--find-renames", "-U3"]
    if mode.kind == "staged":
        return [*base, "--cached"]
    if mode.kind == "base":
        # Three-dot (merge-base) semantics, matching what a PR shows. A two-dot
        # diff would blame this branch for unrelated drift on the base.
        return [*base, f"{mode.ref}...HEAD"]
    return [*base, "HEAD"]


def acquire_diff(mode: DiffMode, cwd: str | Path) -> str:
    """Raw unified diff text for the requested mode."""
    try:
        return run_git(_git_diff_args(mode), cwd)
    except GitError as exc:
        message = str(exc)
        if mode.kind == "base" and re.search(
            r"unknown revision|bad revision|ambiguous argument|not a valid object",
            message,
            re.IGNORECASE,
        ):
            raise DiffError(
                f"could not resolve --base ref '{mode.ref}' — is it a valid branch or commit?"
            ) from exc
        raise DiffError(f"failed to read the git diff: {message}") from exc


def get_hunks(mode: DiffMode, cwd: str | Path) -> list[Hunk]:
    """Hunks for the requested mode.

    In working mode, untracked files are synthesized as ``added`` hunks:
    ``git diff HEAD`` cannot see them, but a file an agent just wrote is
    exactly what vouch exists to inspect.
    """
    if not is_inside_repo(cwd):
        raise DiffError("not a git repository — run vouch from inside a git repo.")

    hunks = parse_diff_to_hunks(acquire_diff(mode, cwd))
    if mode.kind == "working":
        hunks.extend(untracked_hunks(cwd))
    return hunks


def parse_diff_to_hunks(diff_text: str) -> list[Hunk]:
    """Parse unified diff text into hunks. Pure."""
    if not diff_text.strip():
        return []

    hunks: list[Hunk] = []
    for section in _split_file_sections(diff_text):
        hunks.extend(_parse_file_section(section))
    return hunks


def _split_file_sections(diff_text: str) -> list[list[str]]:
    sections: list[list[str]] = []
    current: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current:
                sections.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        sections.append(current)
    return sections


def _parse_file_section(lines: list[str]) -> list[Hunk]:
    old_path: str | None = None
    new_path: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    is_new = is_deleted = is_binary = False
    body_start = len(lines)

    for index, line in enumerate(lines):
        if line.startswith("@@"):
            body_start = index
            break
        if line.startswith("new file mode"):
            is_new = True
        elif line.startswith("deleted file mode"):
            is_deleted = True
        elif line.startswith("rename from "):
            rename_from = line.removeprefix("rename from ").strip()
        elif line.startswith("rename to "):
            rename_to = line.removeprefix("rename to ").strip()
        elif line.startswith("--- "):
            old_path = _header_path(line.removeprefix("--- "))
        elif line.startswith("+++ "):
            new_path = _header_path(line.removeprefix("+++ "))
        elif line.startswith("GIT binary patch") or _BINARY_MARKER.match(line):
            is_binary = True

    # Fall back to the "diff --git a/X b/X" line for pure renames and binary
    # changes, which carry no ---/+++ pair.
    if old_path is None and new_path is None:
        old_path, new_path = _paths_from_git_header(lines[0])
    old_path = old_path or rename_from
    new_path = new_path or rename_to

    renamed = bool(rename_from and rename_to and rename_from != rename_to)
    status: FileStatus = (
        "binary"
        if is_binary
        else "added"
        if is_new
        else "deleted"
        if is_deleted
        else "renamed"
        if renamed
        else "modified"
    )

    path = (old_path if status == "deleted" else new_path) or old_path or ""
    old_file = rename_from if renamed else None

    body = lines[body_start:]
    if not body:
        # No textual hunks: a binary change, or a mode-only change. Both carry
        # no lines but are still worth surfacing to the checks and the agent.
        return [
            Hunk(
                file=path,
                old_file=old_file,
                status=status,
                old_start=0,
                old_lines=0,
                new_start=0,
                new_lines=0,
                lines=[],
            )
        ]

    return _parse_hunks(body, path, old_file, status)


def _parse_hunks(
    body: list[str],
    path: str,
    old_file: str | None,
    status: FileStatus,
) -> list[Hunk]:
    hunks: list[Hunk] = []
    current: Hunk | None = None
    old_no = new_no = 0

    for line in body:
        header = _HUNK_HEADER.match(line)
        if header:
            old_start, old_count, new_start, new_count = header.groups()
            current = Hunk(
                file=path,
                old_file=old_file,
                status=status,
                old_start=int(old_start),
                # An omitted count means 1 in unified diff format.
                old_lines=int(old_count) if old_count is not None else 1,
                new_start=int(new_start),
                new_lines=int(new_count) if new_count is not None else 1,
                lines=[],
            )
            hunks.append(current)
            old_no, new_no = current.old_start, current.new_start
            continue

        if current is None:
            continue
        # "\ No newline at end of file" is metadata, not content.
        if line.startswith("\\"):
            continue

        if line.startswith("+"):
            current.lines.append(HunkLine(kind="add", text=line[1:], new_line=new_no))
            new_no += 1
        elif line.startswith("-"):
            current.lines.append(HunkLine(kind="del", text=line[1:], old_line=old_no))
            old_no += 1
        elif line.startswith(" ") or line == "":
            # A blank line is an empty context line whose leading space some
            # tools strip; treating it as context keeps line numbers aligned.
            current.lines.append(
                HunkLine(kind="context", text=line[1:], old_line=old_no, new_line=new_no)
            )
            old_no += 1
            new_no += 1

    return hunks


def _header_path(raw: str) -> str | None:
    """Strip the a/ or b/ prefix and any trailing tab metadata. /dev/null → None."""
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _paths_from_git_header(header: str) -> tuple[str | None, str | None]:
    """Split ``diff --git a/X b/Y``.

    Paths may contain spaces, so anchor on the final " b/" rather than
    splitting on whitespace.
    """
    rest = header.removeprefix("diff --git ").strip()
    pivot = rest.rfind(" b/")
    if not rest.startswith("a/") or pivot == -1:
        return None, None
    return rest[2:pivot], rest[pivot + 3 :]


def untracked_hunks(cwd: str | Path) -> list[Hunk]:
    """Synthesize whole-file ``added`` hunks for untracked files."""
    root = Path(cwd)
    hunks: list[Hunk] = []
    for git_path in untracked_files(cwd):
        try:
            raw = (root / git_path).read_bytes()
        except OSError:
            continue  # vanished or unreadable between listing and reading
        if b"\0" in raw:
            hunks.append(
                Hunk(
                    file=git_path,
                    old_file=None,
                    status="binary",
                    old_start=0,
                    old_lines=0,
                    new_start=0,
                    new_lines=0,
                    lines=[],
                )
            )
            continue

        text = raw.decode("utf-8", errors="replace")
        text_lines = text.split("\n")
        if text_lines and text_lines[-1] == "":
            text_lines.pop()  # trailing newline, not a final empty line
        hunks.append(
            Hunk(
                file=git_path,
                old_file=None,
                status="added",
                old_start=0,
                old_lines=0,
                new_start=1,
                new_lines=len(text_lines),
                lines=[
                    HunkLine(kind="add", text=line.removesuffix("\r"), new_line=index)
                    for index, line in enumerate(text_lines, start=1)
                ],
            )
        )
    return hunks
