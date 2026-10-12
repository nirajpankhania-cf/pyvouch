#!/usr/bin/env python
"""Populate the run store with real reviews, for demos and manual UI work.

Not fixtures: this builds a scratch git repo, applies a sequence of diffs a
careless agent might plausibly produce, and runs the actual pipeline over each
one. Every row in the store is a genuine report, so the workspace shows real
classifications and real costs rather than something hand-written.

    python scripts/seed_demo.py                 # Pass 1 only, free, no key
    python scripts/seed_demo.py --agent         # both passes, costs tokens

Run it inside the project venv, since it imports vouch itself.

Writes to $VOUCH_DB, else the same default path the API and CLI use, so the
runs appear in the UI with no further wiring.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from vouch.context.diff import DiffMode
from vouch.core.pipeline import PipelineOptions, run_pipeline
from vouch.core.types import TaskInfo
from vouch.envfile import load_env_file
from vouch.store.runs import RunStore, default_db_path

DEMO_REPO = Path("/tmp/vouch-demo-seed")

BASE_FILES: dict[str, str] = {
    "pyproject.toml": """\
        [project]
        name = "shop"
        version = "0.1.0"
        dependencies = ["requests"]
        """,
    "src/shop/__init__.py": "",
    "src/shop/client.py": """\
        \"\"\"HTTP client for the pricing service.\"\"\"

        import urllib.request


        class PricingClient:
            def __init__(self, base_url: str) -> None:
                self.base_url = base_url

            def fetch_price(self, sku: str) -> float:
                url = f"{self.base_url}/price/{sku}"
                with urllib.request.urlopen(url) as response:
                    return float(response.read())
        """,
    "src/shop/cart.py": """\
        \"\"\"Cart totals.\"\"\"

        from shop.client import PricingClient


        class Cart:
            def __init__(self, client: PricingClient) -> None:
                self.client = client
                self.items: list[str] = []

            def add(self, sku: str) -> None:
                self.items.append(sku)

            def total(self) -> float:
                return sum(self.client.fetch_price(sku) for sku in self.items)
        """,
    "src/shop/discount.py": """\
        \"\"\"Discount rules.\"\"\"


        def apply_percentage(total: float, percent: float) -> float:
            return total * (1 - percent / 100)
        """,
    "tests/test_cart.py": """\
        from shop.cart import Cart


        class FakeClient:
            def fetch_price(self, sku: str) -> float:
                return 10.0


        def test_total_sums_prices():
            cart = Cart(FakeClient())
            cart.add("a")
            cart.add("b")
            assert cart.total() == 20.0


        def test_empty_cart_is_free():
            assert Cart(FakeClient()).total() == 0.0
        """,
    "tests/test_discount.py": """\
        from shop.discount import apply_percentage


        def test_ten_percent_off():
            assert apply_percentage(100.0, 10.0) == 90.0


        def test_nothing_off():
            assert apply_percentage(100.0, 0.0) == 100.0
        """,
}


@dataclass
class Scenario:
    """One task plus the diff an agent produced for it."""

    name: str
    task: str
    note: str
    write: dict[str, str] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    Scenario(
        name="retry, done properly",
        task="add retry with exponential backoff to PricingClient.fetch_price",
        note="expect clean — the helper exists, has a test, nothing else touched",
        write={
            "src/shop/backoff.py": """\
                \"\"\"Retry delays.\"\"\"


                def exponential_delay(attempt: int, base: float = 0.5) -> float:
                    \"\"\"Seconds to wait before the given retry attempt.\"\"\"
                    return base * (2**attempt)
                """,
            "src/shop/client.py": """\
                \"\"\"HTTP client for the pricing service.\"\"\"

                import time
                import urllib.request

                from shop.backoff import exponential_delay


                class PricingClient:
                    def __init__(self, base_url: str, max_retries: int = 3) -> None:
                        self.base_url = base_url
                        self.max_retries = max_retries

                    def fetch_price(self, sku: str) -> float:
                        url = f"{self.base_url}/price/{sku}"
                        last_error: Exception | None = None
                        for attempt in range(self.max_retries):
                            try:
                                with urllib.request.urlopen(url) as response:
                                    return float(response.read())
                            except OSError as error:
                                last_error = error
                                time.sleep(exponential_delay(attempt))
                        raise RuntimeError(f"could not fetch {sku}") from last_error
                """,
            "tests/test_backoff.py": """\
                from shop.backoff import exponential_delay


                def test_delay_doubles_each_attempt():
                    assert exponential_delay(0) == 0.5
                    assert exponential_delay(1) == 1.0
                    assert exponential_delay(2) == 2.0
                """,
        },
    ),
    Scenario(
        name="retry, with a hallucinated helper",
        task="add retry with exponential backoff to PricingClient.fetch_price",
        note="expect fail — imports a module nobody wrote, plus a stub and a skipped test",
        write={
            "src/shop/client.py": """\
                \"\"\"HTTP client for the pricing service.\"\"\"

                import time
                import urllib.request

                from shop.backoff import exponential_delay


                class PricingClient:
                    def __init__(self, base_url: str, max_retries: int = 3) -> None:
                        self.base_url = base_url
                        self.max_retries = max_retries

                    def fetch_price(self, sku: str) -> float:
                        url = f"{self.base_url}/price/{sku}"
                        for attempt in range(self.max_retries):
                            try:
                                with urllib.request.urlopen(url) as response:
                                    return float(response.read())
                            except OSError:
                                time.sleep(exponential_delay(attempt))
                        raise RuntimeError(f"could not fetch {sku}")

                    def warm_cache(self, skus: list[str]) -> None:
                        # TODO: implement once the cache service lands
                        pass
                """,
            "tests/test_cart.py": """\
                import pytest

                from shop.cart import Cart


                class FakeClient:
                    def fetch_price(self, sku: str) -> float:
                        return 10.0


                @pytest.mark.skip(reason="flaky in CI")
                def test_total_sums_prices():
                    cart = Cart(FakeClient())
                    cart.add("a")
                    cart.add("b")
                    assert cart.total() == 20.0


                def test_empty_cart_is_free():
                    assert Cart(FakeClient()).total() == 0.0
                """,
        },
    ),
    Scenario(
        name="scope creep, nothing broken",
        task="add a quantity argument to Cart.add",
        note="expect review — the task is done, but an unrelated file came along",
        write={
            "src/shop/cart.py": """\
                \"\"\"Cart totals.\"\"\"

                from shop.client import PricingClient


                class Cart:
                    def __init__(self, client: PricingClient) -> None:
                        self.client = client
                        self.items: list[tuple[str, int]] = []

                    def add(self, sku: str, quantity: int = 1) -> None:
                        self.items.append((sku, quantity))

                    def total(self) -> float:
                        return sum(
                            self.client.fetch_price(sku) * quantity
                            for sku, quantity in self.items
                        )
                """,
            "src/shop/discount.py": """\
                \"\"\"Discount rules.\"\"\"

                import logging

                logger = logging.getLogger(__name__)

                MAX_PERCENT = 90.0


                def apply_percentage(total: float, percent: float) -> float:
                    capped = min(percent, MAX_PERCENT)
                    logger.debug("applying %s%% to %s", capped, total)
                    return total * (1 - capped / 100)
                """,
        },
    ),
    Scenario(
        name="tests deleted instead of fixed",
        task="make apply_percentage reject a negative percentage",
        note="expect fail — a test file was removed rather than updated",
        write={
            "src/shop/discount.py": """\
                \"\"\"Discount rules.\"\"\"


                def apply_percentage(total: float, percent: float) -> float:
                    if percent < 0:
                        raise ValueError("percent must not be negative")
                    return total * (1 - percent / 100)
                """,
        },
        delete=["tests/test_discount.py"],
    ),
    Scenario(
        name="the request was never actually done",
        task="add a currency field to Cart so totals can be shown in GBP or USD",
        note="expect review — plausible-looking edits that never add the field",
        write={
            "src/shop/cart.py": """\
                \"\"\"Cart totals.\"\"\"

                from shop.client import PricingClient


                class Cart:
                    \"\"\"A shopping cart.

                    Totals are returned in the store's base currency.
                    \"\"\"

                    def __init__(self, client: PricingClient) -> None:
                        self.client = client
                        self.items: list[str] = []

                    def add(self, sku: str) -> None:
                        \"\"\"Add one unit of ``sku`` to the cart.\"\"\"
                        self.items.append(sku)

                    def total(self) -> float:
                        \"\"\"Sum the price of every item in the cart.\"\"\"
                        return sum(self.client.fetch_price(sku) for sku in self.items)

                    def format_total(self) -> str:
                        # TODO: respect the cart currency once it exists
                        return f"{self.total():.2f}"
                """,
        },
    ),
]


def git(*args: str, cwd: Path = DEMO_REPO) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def write_files(files: dict[str, str]) -> None:
    for relative, body in files.items():
        path = DEMO_REPO / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        action="store_true",
        help="run Pass 2 as well (needs ANTHROPIC_API_KEY; costs tokens)",
    )
    parser.add_argument("--db", help="store location (default: the same one the API uses)")
    parser.add_argument("--reset", action="store_true", help="delete existing runs first")
    parser.add_argument(
        "--leave",
        type=int,
        default=2,
        metavar="N",
        help="leave scenario N's diff uncommitted so a live review has something "
        "to find (0 for a clean tree)",
    )
    args = parser.parse_args()

    load_env_file(Path(__file__).resolve().parents[1])

    db_path = Path(args.db) if args.db else default_db_path()
    if args.reset and db_path.exists():
        db_path.unlink()
    store = RunStore(db_path)

    if DEMO_REPO.exists():
        shutil.rmtree(DEMO_REPO)
    DEMO_REPO.mkdir(parents=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "demo@example.com")
    git("config", "user.name", "Demo")
    write_files(BASE_FILES)
    git("add", "-A")
    git("commit", "-qm", "initial: pricing client, cart, discounts, tests")

    print(f"demo repo : {DEMO_REPO}")
    print(f"store     : {db_path}")
    print(f"pass 2    : {'on' if args.agent else 'off'}\n")

    for index, scenario in enumerate(SCENARIOS, start=1):
        # Each scenario is an independent uncommitted diff on the same base.
        git("reset", "-q", "--hard", "HEAD")
        git("clean", "-qfd")
        write_files(scenario.write)
        for relative in scenario.delete:
            (DEMO_REPO / relative).unlink(missing_ok=True)

        result = run_pipeline(
            PipelineOptions(
                mode=DiffMode.working(),
                cwd=str(DEMO_REPO),
                task=TaskInfo(text=scenario.task, source="api"),
                agent=args.agent,
                prompt=False,
                use_transcript=False,
            )
        )
        store.save(result.report, result.duration_ms)

        report = result.report
        findings = len(report.deterministic) + len(report.agent.findings or [])
        print(f"{index}. {scenario.name}")
        print(f"   {scenario.note}")
        print(
            f"   -> {report.verdict.upper():6} {findings} finding(s), "
            f"{len(report.changes)} hunk(s), {result.duration_ms:.0f}ms"
            + (f", {report.agent.cost.input_tokens} in-tokens" if report.agent.cost else "")
        )
        if result.agent_status not in ("ran", "disabled"):
            print(f"   note: pass 2 {result.agent_status}")

    git("reset", "-q", "--hard", "HEAD")
    git("clean", "-qfd")
    if 1 <= args.leave <= len(SCENARIOS):
        left = SCENARIOS[args.leave - 1]
        write_files(left.write)
        for relative in left.delete:
            (DEMO_REPO / relative).unlink(missing_ok=True)
        print(f"\nleft uncommitted: {left.name!r}")
        print(f'  task: "{left.task}"')

    print(f"\n{store.count()} run(s) in the store. Serve them with:")
    print(f"  VOUCH_REPO_ROOT={DEMO_REPO} uvicorn vouch.api.app:app --port 8741")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
