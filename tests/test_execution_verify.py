"""
The code team's verification gate must be GROUND TRUTH, not model opinion.

These check the one property that matters: a NameError-class bug (which
compile() cannot see) is caught, while a perfectly good script that happens to
have a __main__ block is NOT falsely failed.
"""
import asyncio

from tools.code_executor import executor


def _run(code: str):
    # Same harness teams/code.py::_verify builds.
    return asyncio.run(executor.run('__name__ = "_vibeai_smoke"\n' + code))


def test_nameerror_is_caught():
    """compile() passes this; only execution catches it."""
    code = "def go():\n    return 1\n\nresult = undefined_symbol()\n"
    assert compile(code, "<t>", "exec")          # syntax check says fine
    r = _run(code)
    assert not r.success
    assert "NameError" in r.stderr


def test_bad_import_is_caught():
    r = _run("import definitely_not_a_real_module_xyz\n")
    assert not r.success
    assert "ModuleNotFoundError" in r.stderr or "ImportError" in r.stderr


def test_main_guard_does_not_run():
    """
    The regression this harness exists to prevent: a script whose __main__
    block needs argv/stdin must not be reported as broken. Reassigning
    __name__ keeps it an import test.
    """
    code = (
        "import sys\n"
        "def main():\n"
        "    raise SystemExit('needs real argv')\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    r = _run(code)
    assert r.success, r.stderr


def test_failing_toplevel_assert_is_caught():
    r = _run("x = 1\nassert x == 2, 'self-check failed'\n")
    assert not r.success
    assert "AssertionError" in r.stderr


def test_clean_code_passes():
    r = _run("def add(a, b):\n    return a + b\n\nassert add(2, 2) == 4\n")
    assert r.success, r.stderr


def test_best_of_n_skips_judge_when_one_candidate_executes():
    """Execution beats judge-model opinion: if only one of three drafts
    actually imports cleanly, best-of-N must return it without ever calling
    the judge model."""
    import teams.code as code_mod

    class _FakeModel:
        def __init__(self, model_id):
            self.model_id = model_id

        async def generate(self, prompt="", **kwargs):
            if self.model_id == "gpt_oss_120b_coder":
                return "```python\ndef add(a, b):\n    return a + b\n```"
            if self.model_id == "glm_47_cerebras":
                return "```python\nresult = undefined_symbol()\n```"
            if self.model_id == "nemotron_super_bulk":
                return "```python\nimport definitely_not_a_real_module_xyz\n```"
            raise AssertionError(f"judge model must not be called, got {self.model_id}")

    team = code_mod.CodeTeam()
    team._get_model = lambda model_id: _FakeModel(model_id)

    result = asyncio.run(team._best_of_n("add two numbers", budget=500))
    assert "def add" in result


def test_extract_python_ignores_repl_transcript_fence():
    """A debug answer routinely carries a SECOND fence showing usage as a bare
    `>>>` transcript ("show the minimal failing case") alongside the real fix.
    Concatenating every fence (the old behaviour) glued that onto the real
    code -- `>>>` isn't valid top-level Python, so a correct fix was reported
    as broken. Found live via vibe-loop: 5/21 real train answers hit exactly
    this, all with otherwise-correct fixes."""
    from teams.code import _extract_python

    answer = (
        "**Minimal failing case**\n\n"
        "```python\n>>> factorial(0)\n1\n```\n\n"
        "**Corrected code**\n\n"
        "```python\ndef factorial(n):\n    if n <= 1:\n        return 1\n"
        "    return n * factorial(n - 1)\n```\n\n"
        "Now it works:\n\n"
        "```python\n>>> factorial(0)\n1\n>>> factorial(5)\n120\n```\n"
    )
    code = _extract_python(answer)
    compile(code, "<t>", "exec")  # must not raise -- old code raised SyntaxError here
    assert "def factorial" in code
    assert ">>>" not in code


def test_extract_python_ignores_undefined_reference_demo_fence():
    """The transcript case above is one shape of the same bug; this is the
    other -- a plain (non `>>>`) usage demo fence that references a class
    defined in a LATER fence, which reads as NameError once concatenated."""
    from teams.code import _extract_python

    answer = (
        "```python\ninv = Inventory()\ninv.add('apple', 3)\n```\n\n"
        "### Full corrected code\n\n"
        "```python\nclass Inventory:\n    def __init__(self):\n        self.items = {}\n\n"
        "    def add(self, name, qty):\n        self.items[name] = self.items.get(name, 0) + qty\n```\n"
    )
    code = _extract_python(answer)
    ns = {}
    exec(compile(code, "<t>", "exec"), ns)  # must not raise NameError
    assert "class Inventory" in code


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
