"""Detects fake completeness: stub markers, hand-waves, and empty bodies added.

Two strategies, deliberately split. Comment idioms are line-local, so they are
matched with regex over *added* lines only — the ``add`` filter is what
guarantees a TODO being *removed* never flags.

Empty bodies go through the AST instead, because in Python ``pass`` and ``...``
are idiomatic in abstract methods, ``@overload`` signatures, Protocols, and
``.pyi`` stubs. Regex cannot tell a stub from a legitimate abstract method, so
it shouldn't try.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from vouch.checks.shared import (
    added_line_numbers,
    files_in,
    is_code_file,
    parse_python,
)
from vouch.core.types import CheckContext, Confidence, Finding

CHECK_NAME = "placeholders"
CODE = "placeholder-code"


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    reason: str
    confidence: Confidence


# These idioms are only meaningful in a comment: "not implemented" also appears
# in legitimate code (raise NotImplementedError in an ABC), which the AST pass
# judges with the context needed to tell the two apart.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        re.compile(r"\bnot[ _-]?implemented\b", re.IGNORECASE),
        'explicit "not implemented" comment',
        "high",
    ),
    _Pattern(
        re.compile(
            r"\brest of (?:the )?(?:implementation|file|code|function|logic|method)\b",
            re.IGNORECASE,
        ),
        '"rest of ..." hand-wave',
        "high",
    ),
    _Pattern(
        re.compile(
            r"\b(?:implementation|logic|code|body) (?:goes|would go) here\b",
            re.IGNORECASE,
        ),
        '"goes here" stub comment',
        "high",
    ),
    _Pattern(
        re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b"),
        "TODO/FIXME comment added",
        "medium",
    ),
)

# Decorators that make an empty body correct rather than lazy.
_ABSTRACT_DECORATORS = frozenset(
    {"abstractmethod", "abstractproperty", "overload", "abstractclassmethod"}
)
# Base classes whose methods are expected to be bodyless.
_STUB_BASES = frozenset({"Protocol", "ABC"})


def run(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(_comment_markers(ctx))
    findings.extend(_empty_bodies(ctx))
    return findings


def _comment_markers(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for hunk in ctx.hunks:
        if hunk.status == "binary" or not is_code_file(hunk.file):
            continue
        for line in hunk.lines:
            if line.kind != "add":
                continue
            comment = _comment_of(line.text)
            if comment is None:
                continue
            hit = _first_match(comment)
            if hit is None:
                continue
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    code=CODE,
                    severity="warn",
                    file=hunk.file,
                    line=line.new_line,
                    message=f'{hit.reason}: "{line.text.strip()}"',
                    confidence=hit.confidence,
                )
            )
    return findings


def _comment_of(text: str) -> str | None:
    """The comment portion of a line, ignoring '#' inside string literals."""
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and quote:
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return text[index + 1 :]
        index += 1
    return None


def _first_match(comment: str) -> _Pattern | None:
    """First pattern matching outside quotes.

    A comment that *quotes* a marker — as this module's own comments do when
    documenting them — is discussing the idiom, not declaring a stub.
    """
    quoted = _quoted_spans(comment)
    for pattern in _PATTERNS:
        for match in pattern.regex.finditer(comment):
            if not any(start <= match.start() < end for start, end in quoted):
                return pattern
    return None


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for quote in "\"'":
        position = 0
        while True:
            start = text.find(quote, position)
            if start == -1:
                break
            end = text.find(quote, start + 1)
            if end == -1:
                break
            spans.append((start, end))
            position = end + 1
    return spans


def _empty_bodies(ctx: CheckContext) -> list[Finding]:
    findings: list[Finding] = []
    for file in files_in(ctx.hunks, python_only=True):
        # .pyi files are *supposed* to be nothing but empty bodies.
        if file.endswith(".pyi"):
            continue
        source = ctx.project.files.get(file)
        if source is None:
            continue
        tree = parse_python(source)
        if tree is None:
            continue

        added = added_line_numbers(ctx.hunks, file)
        for function, stub_context in _walk_functions(tree):
            if stub_context or function.lineno not in added:
                continue
            kind = _stub_kind(function)
            if kind is None:
                continue
            findings.append(
                Finding(
                    check=CHECK_NAME,
                    code=CODE,
                    severity="warn",
                    file=file,
                    line=function.lineno,
                    message=f"{kind} added: '{function.name}' has no implementation",
                    confidence="medium",
                )
            )
    return findings


def _walk_functions(tree: ast.Module):
    """Yield (function, in_stub_context) pairs for every function in the module.

    ``in_stub_context`` is True inside a Protocol or ABC body, where a bodyless
    method is a declaration rather than a missing implementation.
    """

    def visit(node: ast.AST, stub: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from visit(child, stub or _is_stub_class(child))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                yield child, stub
                # Nested helpers inherit the enclosing context.
                yield from visit(child, stub)
            else:
                yield from visit(child, stub)

    yield from visit(tree, False)


def _is_stub_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
        if name in _STUB_BASES:
            return True
    for keyword in node.keywords:
        # class Foo(metaclass=ABCMeta)
        if keyword.arg == "metaclass":
            value = keyword.value
            name = value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", None)
            if name == "ABCMeta":
                return True
    return False


def _stub_kind(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Classify a bodyless function, or None if it has a real implementation."""
    if _has_abstract_decorator(function):
        return None

    body = list(function.body)
    if _is_docstring(body[0] if body else None):
        body = body[1:]  # a docstring is not an implementation
    if len(body) != 1:
        return None

    statement = body[0]
    if isinstance(statement, ast.Pass):
        return "empty body"
    if (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    ):
        return "empty body"
    if _raises_not_implemented(statement):
        return "NotImplementedError stub"
    return None


def _is_docstring(statement: ast.stmt | None) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _has_abstract_decorator(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name in _ABSTRACT_DECORATORS:
            return True
    return False


def _raises_not_implemented(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.Raise) or statement.exc is None:
        return False
    raised = statement.exc
    target = raised.func if isinstance(raised, ast.Call) else raised
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
    return name == "NotImplementedError"
