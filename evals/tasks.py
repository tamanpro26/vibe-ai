"""
evals/tasks.py
The fixed task suite for `evals/run_eval.py`. The first two prompts are the
same design + backend/critical-thinking pair used in the manual exam earlier
this project (graded by hand in chat) — formalized here so the same bar can
be re-checked mechanically after any change, instead of re-typing prompts.
Later tasks broaden coverage: different UI patterns, a CLI tool instead of a
library, and a debugging task (pre-seeded broken code) — the original two
tasks only exercise "build something from scratch," never "fix something
that's already wrong," which is a distinct capability worth its own gate.

Add a task by appending an EvalTask; there is no registration step elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvalTask:
    slug:        str              # dir-safe name; becomes evals/runs/<run_id>/<slug>/
    category:    str              # "design" | "backend" — decides which checks apply
    task_type:   str              # passed through to run_agent(task_type=...)
    prompt:      str
    setup_files: dict[str, str] = field(default_factory=dict)
    # relative path -> content, written into the workspace BEFORE the agent
    # runs. Empty for "build from scratch" tasks; non-empty for tasks that
    # need pre-existing (possibly broken) code, e.g. a debugging task.


TASKS: list[EvalTask] = [
    EvalTask(
        slug="design_portfolio",
        category="design",
        task_type="coding",
        prompt=(
            "Build a single-page portfolio site for a freelance photographer named "
            "Aurora Reyes. It needs a hero section with her name and tagline, a "
            "responsive image gallery (grid, at least 6 pieces), an About section, "
            "and a contact form (name/email/message, client-side validated, no "
            "backend needed). Use a cohesive dark, moody color palette that fits "
            "photography. Make it fully responsive down to mobile width. This is a "
            "portfolio for a real photographer — no placeholder text, no lorem "
            "ipsum, no stock 'Skill 1 / Skill 2' filler; every section needs "
            "specific, real-sounding content."
        ),
    ),
    EvalTask(
        slug="backend_rate_limiter",
        category="backend",
        task_type="coding",
        prompt=(
            "Build a small Python backend module implementing a token-bucket rate "
            "limiter as an importable library (not a web framework) — "
            "`RateLimiter(rate: float, capacity: int)` with an `allow(key: str) -> "
            "bool` method that is safe under concurrent asyncio callers (no lost "
            "updates from race conditions), evicts state for keys that haven't "
            "been seen in a configurable TTL so memory doesn't grow unbounded, and "
            "includes a `pytest` test suite covering: burst-then-refill behavior, "
            "two different keys not affecting each other's buckets, concurrent "
            "callers not double-spending tokens, and TTL eviction actually freeing "
            "memory. Explain any concurrency primitive you choose and why it's "
            "correct under asyncio's cooperative scheduling. This is a pure Python "
            "library with no web UI — do not create any HTML/CSS or call any image/"
            "design generation tool."
        ),
    ),
    EvalTask(
        slug="design_saas_dashboard",
        category="design",
        task_type="coding",
        prompt=(
            "Build a single-page analytics dashboard for a SaaS product called "
            "'Pulsegrid'. It needs: a sidebar nav, a top row of 4 KPI stat cards "
            "(with real-looking numbers, not '0' or 'N/A'), at least one chart "
            "(a simple SVG or canvas-based line or bar chart is fine — no chart "
            "library dependency required), a data table of recent activity with "
            "at least 8 rows of realistic sample data, and a working dark-mode "
            "toggle that actually restyles the page. Fully responsive down to "
            "mobile (sidebar should collapse). No placeholder text, no lorem "
            "ipsum, no 'Lorem/Item 1/Item 2' filler in the table."
        ),
    ),
    EvalTask(
        slug="backend_cli_csv_tool",
        category="backend",
        task_type="coding",
        prompt=(
            "Build a Python command-line tool (using argparse) called `csvstat` "
            "that reads a CSV file and prints summary statistics per numeric "
            "column (min, max, mean, count of nulls). It must: handle a missing "
            "file with a clean error message (no raw traceback), handle a CSV "
            "with no numeric columns gracefully, support a `--column NAME` flag "
            "to restrict output to one column, and read from stdin when given "
            "`-` as the filename instead of a path. Include a pytest test suite "
            "that covers all four of those behaviors using temporary CSV "
            "fixtures (no reliance on any file outside the test). This is a "
            "terminal-only tool with no web UI — do not create any HTML/CSS or "
            "call any image/design generation tool."
        ),
    ),
    EvalTask(
        slug="backend_debug_inventory",
        category="backend",
        task_type="debugging",
        setup_files={
            "inventory.py": (
                "class Inventory:\n"
                "    def __init__(self):\n"
                "        self.items = {}\n"
                "\n"
                "    def add(self, name, qty):\n"
                "        self.items[name] = self.items[name] + qty\n"
                "\n"
                "    def remove(self, name, qty):\n"
                "        self.items[name] -= qty\n"
                "        if self.items[name] < 0:\n"
                "            self.items[name] = 0\n"
                "\n"
                "    def total_items(self):\n"
                "        total = 0\n"
                "        for qty in self.items:\n"
                "            total += qty\n"
                "        return total\n"
                "\n"
                "    def low_stock(self, threshold=5):\n"
                "        return [name for name, qty in self.items if qty < threshold]\n"
            ),
            "tests/test_inventory.py": (
                "import sys, os\n"
                "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\n"
                "from inventory import Inventory\n"
                "\n"
                "def test_add_new_item():\n"
                "    inv = Inventory()\n"
                "    inv.add('widget', 10)\n"
                "    assert inv.items['widget'] == 10\n"
                "\n"
                "def test_add_existing_item_accumulates():\n"
                "    inv = Inventory()\n"
                "    inv.add('widget', 10)\n"
                "    inv.add('widget', 5)\n"
                "    assert inv.items['widget'] == 15\n"
                "\n"
                "def test_remove_floors_at_zero():\n"
                "    inv = Inventory()\n"
                "    inv.add('widget', 3)\n"
                "    inv.remove('widget', 10)\n"
                "    assert inv.items['widget'] == 0\n"
                "\n"
                "def test_total_items():\n"
                "    inv = Inventory()\n"
                "    inv.add('widget', 10)\n"
                "    inv.add('gadget', 5)\n"
                "    assert inv.total_items() == 15\n"
                "\n"
                "def test_low_stock():\n"
                "    inv = Inventory()\n"
                "    inv.add('widget', 2)\n"
                "    inv.add('gadget', 20)\n"
                "    assert inv.low_stock(threshold=5) == ['widget']\n"
            ),
        },
        prompt=(
            "The inventory.py module in this project has bugs — running its test "
            "suite (tests/test_inventory.py) fails. Real bugs to find: `add()` "
            "crashes with a KeyError the first time a new item name is added (it "
            "reads self.items[name] before ever setting it); `total_items()` and "
            "`low_stock()` both iterate over the dict incorrectly — iterating a "
            "dict directly yields its KEYS only, not (key, value) pairs, so "
            "`total_items()` raises a TypeError trying to add a string to an int, "
            "and `low_stock()`'s `for name, qty in self.items` raises a ValueError "
            "trying to unpack a single string key into two variables. Find and fix "
            "all the actual bugs so the existing test suite passes. Do not rewrite "
            "the tests — they encode the correct expected behavior. This is a pure "
            "Python module with no web UI — do not create any HTML/CSS or call any "
            "image/design generation tool."
        ),
    ),
]
