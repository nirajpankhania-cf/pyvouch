"""The Pass 1 check registry.

Order is the order findings appear in a report when severities tie.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vouch.checks import imports, placeholders, scope, tests
from vouch.core.types import CheckContext, Finding


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[CheckContext], list[Finding]]


ALL_CHECKS: tuple[Check, ...] = (
    Check("placeholders", placeholders.run),
    Check("tests", tests.run),
    Check("imports", imports.run),
    Check("scope", scope.run),
)

CHECK_NAMES: tuple[str, ...] = tuple(check.name for check in ALL_CHECKS)
