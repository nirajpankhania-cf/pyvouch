"""Catches hallucinated imports: modules that don't exist and names they don't define.

Three resolution paths, each with a different confidence story:

* **Relative imports** resolve against the repo's own module index. Always
  checkable, regardless of environment.
* **Project-internal absolute imports** resolve the same way, and additionally
  verify that ``from x import y`` names something ``x`` actually defines.
* **Third-party imports** need the target repo's environment. When none was
  discovered the check declines to judge them, rather than reporting every
  real dependency as hallucinated.

Only imports on *added* lines are considered, so pre-existing breakage is not
attributed to this diff.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from vouch.checks.shared import added_line_numbers, files_in, parse_python
from vouch.core.types import CheckContext, Finding, ProjectAccess

CHECK_NAME = "imports"
CODE = "unresolved-import"


@dataclass(frozen=True)
class _Import:
    line: int
    module: str | None  # None for "from . import x"
    level: int  # 0 = absolute
    names: tuple[tuple[str, str | None], ...]  # (name, alias)
    statement: str


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for file in files_in(ctx.hunks, python_only=True):
        source = ctx.project.files.get(file)
        if source is None:
            continue
        tree = parse_python(source)
        if tree is None:
            continue

        added = added_line_numbers(ctx.hunks, file)
        guarded = _guarded_lines(tree)
        for ref in _imports_in(tree):
            if ref.line not in added or ref.line in guarded:
                continue
            findings.extend(_check_import(ref, file, ctx.project))
    return findings


_IMPORT_ERRORS = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


def _guarded_lines(tree: ast.Module) -> set[int]:
    """Lines inside a ``try`` that handles ImportError.

    An import allowed to fail is an optional dependency, not a hallucination.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _catches_import_error(node):
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                line = getattr(inner, "lineno", None)
                if line is not None:
                    lines.add(line)
    return lines


def _catches_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        if handler.type is None:  # bare except
            return True
        candidates = (
            handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        )
        for candidate in candidates:
            name = (
                candidate.attr
                if isinstance(candidate, ast.Attribute)
                else getattr(candidate, "id", None)
            )
            if name in _IMPORT_ERRORS:
                return True
    return False


def _imports_in(tree: ast.Module) -> list[_Import]:
    imports: list[_Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    _Import(
                        line=node.lineno,
                        module=alias.name,
                        level=0,
                        names=(),
                        statement=f"import {alias.name}",
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            names = tuple((alias.name, alias.asname) for alias in node.names)
            dots = "." * node.level
            imports.append(
                _Import(
                    line=node.lineno,
                    module=node.module,
                    level=node.level,
                    names=names,
                    statement=f"from {dots}{node.module or ''} import "
                    + ", ".join(name for name, _ in names),
                )
            )
    return imports


def _check_import(ref: _Import, file: str, project: ProjectAccess) -> list[Finding]:
    if ref.level > 0:
        return _check_relative(ref, file, project)
    return _check_absolute(ref, file, project)


def _check_relative(ref: _Import, file: str, project: ProjectAccess) -> list[Finding]:
    package = _package_of(file)
    # level 1 is the containing package; each extra dot goes one level up.
    for _ in range(ref.level - 1):
        if not package:
            return [
                _finding(
                    file,
                    ref.line,
                    f"relative import goes above the package root: {ref.statement}",
                    "error",
                    "high",
                )
            ]
        package = package.rsplit(".", 1)[0] if "." in package else ""

    target = f"{package}.{ref.module}" if ref.module else package
    target = target.strip(".")

    if ref.module and target not in project.module_files:
        return [
            _finding(
                file,
                ref.line,
                f"relative import doesn't resolve to a module in this repo: {ref.statement}",
                "error",
                "high",
            )
        ]
    return _check_names(ref, file, target, project)


def _check_absolute(ref: _Import, file: str, project: ProjectAccess) -> list[Finding]:
    module = ref.module
    if not module:
        return []
    top_level = module.split(".")[0]

    # Project-internal: fully verifiable from the repo itself.
    if top_level in _project_top_levels(project):
        if module in project.module_files:
            return _check_names(ref, file, module, project)
        if _is_namespace_prefix(module, project):
            return []
        return [
            _finding(
                file,
                ref.line,
                f"module not found in this repo: {ref.statement}",
                "error",
                "high",
            )
        ]

    if top_level in project.installed_modules:
        return []

    # A module sitting next to the importing file: Python and pytest both put
    # that directory on sys.path.
    directory = file.rsplit("/", 1)[0] if "/" in file else ""
    if top_level in project.sibling_modules.get(directory, set()):
        return []

    if not project.env_discovered:
        # No environment to check against. Saying nothing beats guessing.
        return []

    return [
        _finding(
            file,
            ref.line,
            f"'{top_level}' is not installed in this project's environment: {ref.statement}",
            "error",
            "high",
        )
    ]


def _check_names(
    ref: _Import,
    file: str,
    module: str,
    project: ProjectAccess,
) -> list[Finding]:
    """Verify ``from module import name`` against what the module defines."""
    if not ref.names or module in project.opaque_modules:
        return []
    exports = project.module_exports.get(module)
    if exports is None:
        return []

    findings: list[Finding] = []
    for name, _alias in ref.names:
        if name == "*" or name in exports:
            continue
        # A submodule of a package is importable without being a name in
        # its __init__.py.
        if f"{module}.{name}" in project.module_files:
            continue
        findings.append(
            _finding(
                file,
                ref.line,
                f"'{module}' has no '{name}': {ref.statement}",
                "error",
                "high",
            )
        )
    return findings


def _project_top_levels(project: ProjectAccess) -> set[str]:
    return {dotted.split(".")[0] for dotted in project.module_files}


def _is_namespace_prefix(module: str, project: ProjectAccess) -> bool:
    """True if the module is a package path with children but no own file.

    Namespace packages have no ``__init__.py``, so they never appear in the
    index themselves while their children do.
    """
    prefix = f"{module}."
    return any(dotted.startswith(prefix) for dotted in project.module_files)


def _package_of(file: str) -> str:
    """Dotted package containing a file, as the module index names it."""
    parts = file.split("/")[:-1]
    for container in ("src", "lib"):
        if parts and parts[0] == container:
            parts = parts[1:]
            break
    return ".".join(part for part in parts if part.isidentifier())


def _finding(file: str, line: int, message: str, severity: str, confidence: str) -> Finding:
    return Finding(
        check=CHECK_NAME,
        code=CODE,
        severity=severity,
        file=file,
        line=line,
        message=message,
        confidence=confidence,
    )
