"""Project access: file contents and the target repo's import surface.

Built once per run so checks stay I/O-free.

The environment we resolve imports against is the *target repo's*, never
vouch's own. Resolving against vouch's ``site-packages`` would report every
real dependency of the project under review as a hallucinated import. When no
environment can be found we say so via ``env_discovered`` and the imports
check drops to low confidence rather than guessing.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from vouch.core.types import Hunk, ProjectAccess

# Conventional in-repo virtualenv locations, most common first.
_VENV_DIRS = (".venv", "venv", ".virtualenv", "env")

# Distribution name -> import name, where they differ and the package is
# common enough to matter. Only consulted on the declared-dependency
# fallback path, which is already low confidence.
_IMPORT_NAME_OVERRIDES = {
    "attrs": "attr",
    "beautifulsoup4": "bs4",
    "pillow": "PIL",
    "protobuf": "google",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "python-dotenv": "dotenv",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "msgpack-python": "msgpack",
    "typing-extensions": "typing_extensions",
}

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def build_project_access(hunks: list[Hunk], cwd: str | Path) -> ProjectAccess:
    root = Path(cwd)
    site_packages = _find_site_packages(root)
    installed = set(sys.stdlib_module_names)
    installed |= _project_top_level(root)

    if site_packages is not None:
        installed |= _modules_in_site_packages(site_packages)
        discovered = True
    else:
        installed |= _declared_dependencies(root)
        discovered = False

    index = _build_module_index(root)
    return ProjectAccess(
        repo_root=str(root),
        files=_read_touched_files(hunks, root),
        installed_modules=installed,
        env_discovered=discovered,
        module_files=index.files,
        module_exports=index.exports,
        opaque_modules=index.opaque,
        sibling_modules=_sibling_modules(root),
    )


def _sibling_modules(root: Path) -> dict[str, set[str]]:
    """Modules importable as siblings, keyed by the directory holding them.

    Python puts a script's own directory on ``sys.path``, and pytest does the
    same for test directories, so ``from conftest import ...`` inside
    ``tests/`` resolves even though ``tests`` is not a package.
    """
    result: dict[str, set[str]] = {}
    for path in _walk_python(root):
        parent = path.parent
        key = parent.relative_to(root).as_posix() if parent != root else ""
        if path.stem.isidentifier():
            result.setdefault(key, set()).add(path.stem)
    return result


# Directories never worth walking for the module index.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".virtualenv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "build",
        "dist",
        "site-packages",
    }
)

# Parsing is cheap per file but unbounded repos are not. Past this, the
# imports check still works on the environment, just without export checking.
_MAX_INDEXED_FILES = 4000


@dataclass
class _ModuleIndex:
    files: dict[str, str]
    exports: dict[str, set[str]]
    opaque: set[str]


def _build_module_index(root: Path) -> _ModuleIndex:
    """Map the repo's own modules to their paths and top-level names.

    Both flat and src layouts are indexed, so ``import mypkg.thing`` resolves
    whether the package sits at the root or under ``src/``.
    """
    index = _ModuleIndex(files={}, exports={}, opaque=set())
    count = 0

    for container in ("", "src", "lib"):
        source_root = root / container if container else root
        if not source_root.is_dir():
            continue
        for path in _walk_python(source_root):
            if count >= _MAX_INDEXED_FILES:
                return index
            count += 1
            dotted = _dotted_name(path.relative_to(source_root))
            if not dotted or dotted in index.files:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            index.files[dotted] = path.relative_to(root).as_posix()
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError, RecursionError):
                # Unparseable: record it exists, but claim nothing about names.
                index.opaque.add(dotted)
                continue
            names, opaque = _top_level_names(tree)
            index.exports[dotted] = names
            if opaque:
                index.opaque.add(dotted)
    return index


def _walk_python(source_root: Path):
    stack = [source_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS and not entry.name.endswith((".egg-info",)):
                    stack.append(entry)
            elif entry.suffix == ".py":
                yield entry


def _dotted_name(relative: Path) -> str:
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][:-3]
    if not parts or not all(part.isidentifier() for part in parts):
        return ""
    return ".".join(parts)


def _top_level_names(tree: ast.Module) -> tuple[set[str], bool]:
    """Public top-level names, plus whether the module re-exports dynamically.

    ``__all__`` wins when present. A star import or module-level
    ``__getattr__`` makes absence meaningless, so the module is marked opaque.
    """
    names: set[str] = set()
    opaque = False
    explicit: set[str] | None = None

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            if node.name == "__getattr__":
                opaque = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                    if target.id == "__all__":
                        explicit = _literal_strings(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    opaque = True
                else:
                    names.add(alias.asname or alias.name)

    if explicit is not None:
        # __all__ is the module's own statement of intent, but names defined
        # outside it are still importable, so keep both.
        names |= explicit
    return names, opaque


def _literal_strings(node: ast.expr) -> set[str]:
    if not isinstance(node, ast.List | ast.Tuple | ast.Set):
        return set()
    return {
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


def _read_touched_files(hunks: list[Hunk], root: Path) -> dict[str, str]:
    """Post-change contents of diff-touched files, keyed by git-normalized path."""
    files: dict[str, str] = {}
    for hunk in hunks:
        if hunk.status in ("deleted", "binary") or hunk.file in files:
            continue
        try:
            files[hunk.file] = (root / hunk.file).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue  # unreadable or not valid UTF-8; checks skip what's absent
    return files


def _find_site_packages(root: Path) -> Path | None:
    """Locate the target repo's site-packages, ignoring vouch's own."""
    own_prefix = Path(sys.prefix).resolve()
    for name in _VENV_DIRS:
        candidate = root / name
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved == own_prefix:
            # vouch is running from the target repo's own venv, which is the
            # correct environment to resolve against.
            return _site_packages_of(resolved)
        found = _site_packages_of(resolved)
        if found is not None:
            return found
    return None


def _site_packages_of(venv: Path) -> Path | None:
    windows = venv / "Lib" / "site-packages"
    if windows.is_dir():
        return windows
    matches = sorted((venv / "lib").glob("python3.*/site-packages"))
    return matches[-1] if matches else None


def _modules_in_site_packages(site_packages: Path) -> set[str]:
    names: set[str] = set()
    try:
        entries = list(site_packages.iterdir())
    except OSError:
        return names

    for entry in entries:
        name = entry.name
        if entry.is_dir():
            if name.endswith((".dist-info", ".egg-info", "__pycache__")):
                continue
            if name.isidentifier():
                names.add(name)
        elif name.endswith(".py"):
            stem = name[:-3]
            if stem.isidentifier():
                names.add(stem)
        elif name.endswith((".so", ".pyd")):
            # Extension modules: "_mod.cpython-313-darwin.so" -> "_mod"
            stem = name.split(".", 1)[0]
            if stem.isidentifier():
                names.add(stem)

    names |= _declared_top_level(site_packages)
    names |= _editable_targets(site_packages)
    return names


def _declared_top_level(site_packages: Path) -> set[str]:
    """Read top_level.txt from installed distributions."""
    names: set[str] = set()
    for metadata in site_packages.glob("*.dist-info/top_level.txt"):
        try:
            content = metadata.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        names.update(line.strip() for line in content.splitlines() if line.strip().isidentifier())
    return names


def _editable_targets(site_packages: Path) -> set[str]:
    """Resolve editable installs, whose packages live outside site-packages."""
    names: set[str] = set()
    for pth in site_packages.glob("*.pth"):
        try:
            lines = pth.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            candidate = line.strip()
            if not candidate or candidate.startswith(("import ", "#")):
                continue
            target = Path(candidate)
            if target.is_dir():
                names |= _top_level_in(target)
    return names


def _project_top_level(root: Path) -> set[str]:
    """Top-level importable names defined by the repo itself.

    Covers both flat layout and src layout, so a project's own modules never
    look unresolved to the imports check.
    """
    names = _top_level_in(root)
    for container in ("src", "lib"):
        directory = root / container
        if directory.is_dir():
            names |= _top_level_in(directory)
    return names


def _top_level_in(directory: Path) -> set[str]:
    names: set[str] = set()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return names
    for entry in entries:
        if entry.is_dir():
            if entry.name.isidentifier() and (entry / "__init__.py").exists():
                names.add(entry.name)
        elif entry.suffix == ".py" and entry.stem.isidentifier():
            names.add(entry.stem)
    return names


def _declared_dependencies(root: Path) -> set[str]:
    """Fallback when no environment exists: trust what the project declares.

    Distribution names only approximate import names, which is part of why
    this path reports ``env_discovered=False``.
    """
    declared: set[str] = set()
    declared |= _pyproject_dependencies(root / "pyproject.toml")
    for pattern in ("requirements*.txt", "requirements/*.txt"):
        for requirements in root.glob(pattern):
            declared |= _requirements_names(requirements)
    return {_to_import_name(name) for name in declared}


def _pyproject_dependencies(path: Path) -> set[str]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return set()

    project = data.get("project", {})
    specs: list[str] = list(project.get("dependencies", []) or [])
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        specs.extend(extra or [])
    names = {_requirement_name(spec) for spec in specs if isinstance(spec, str)}

    # Also count the project's own distribution name as importable.
    own = project.get("name")
    if isinstance(own, str):
        names.add(own)
    return {name for name in names if name}


def _requirements_names(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return set()
    names = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        name = _requirement_name(stripped)
        if name:
            names.add(name)
    return names


def _requirement_name(spec: str) -> str:
    # Strip extras and version markers: "uvicorn[standard]>=0.32" -> "uvicorn"
    head = spec.split(";", 1)[0].split("[", 1)[0]
    match = _REQUIREMENT_NAME.match(head)
    return match.group(1).lower() if match else ""


def _to_import_name(dist_name: str) -> str:
    lowered = dist_name.lower()
    return _IMPORT_NAME_OVERRIDES.get(lowered, lowered.replace("-", "_"))
