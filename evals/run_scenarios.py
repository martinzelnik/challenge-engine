"""Run the hand-checked account scenarios end to end against the engine.

Usage:
    python evals/run_scenarios.py                       # this repo
    python evals/run_scenarios.py --workspace ../run-42 # another checkout

Each file in evals/scenarios/ holds an account (the CLI's JSON format), the
expected outcome, and the arithmetic behind it in `worked_out`. The expected
values were worked out by hand from docs/spec.md, not by running the engine, so
they check the spec reading independently of the tests the agents write.

For every scenario this runs `python -m challenge_engine <file>` (exit code,
verdict line, each rule's status, first breach time) and also calls
`parse_account` + `evaluate` directly to check each rule's `breached_at`.
Exits 1 if any scenario disagrees.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"
VERDICT_LINE = re.compile(r"^Verdict: (\w+)(?: \(first breach at (\S+)\))?$")
RULE_LINE = re.compile(r"^  (\w+): (\w+) - ")


def _instant(value: str | None) -> datetime | None:
    """Parse an ISO datetime so times in different offsets compare as instants."""
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_cli(scenario: dict, account_file: Path, workspace: Path) -> list[str]:
    expect = scenario["expect"]
    env = {**os.environ, "PYTHONPATH": str(workspace / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "challenge_engine", str(account_file)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    problems = []
    if proc.returncode != expect["exit_code"]:
        problems.append(f"exit code {proc.returncode}, expected {expect['exit_code']}")
    lines = proc.stdout.splitlines()
    match = VERDICT_LINE.match(lines[0]) if lines else None
    if not match:
        problems.append(f"no verdict line in output: {proc.stdout[:300]!r} {proc.stderr[-300:]!r}")
        return problems
    verdict, failed_at = match.groups()
    if verdict != expect["verdict"]:
        problems.append(f"CLI verdict {verdict}, expected {expect['verdict']}")
    if _instant(failed_at) != _instant(expect["failed_at"]):
        problems.append(f"CLI first breach {failed_at}, expected {expect['failed_at']}")
    seen = {m.group(1): m.group(2) for m in map(RULE_LINE.match, lines[1:]) if m}
    for rule, want in expect["rules"].items():
        if seen.get(rule) != want["status"]:
            problems.append(f"CLI {rule} {seen.get(rule)}, expected {want['status']}")
    return problems


def check_engine(scenario: dict) -> list[str]:
    from challenge_engine.cli import parse_account
    from challenge_engine.rules import evaluate

    expect = scenario["expect"]
    history, config = parse_account(scenario["account"])
    verdict = evaluate(history, config)
    problems = []
    order = [r.rule for r in verdict.rules]
    if order != list(expect["rules"]):
        problems.append(f"rule order {order}, expected {list(expect['rules'])}")
    for result in verdict.rules:
        want = expect["rules"].get(result.rule)
        if want is None:
            continue
        if result.status != want["status"]:
            problems.append(f"{result.rule} {result.status}, expected {want['status']}")
        if result.breached_at != _instant(want["breached_at"]):
            got = result.breached_at.isoformat() if result.breached_at else None
            problems.append(f"{result.rule} breached_at {got}, expected {want['breached_at']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    sys.path.insert(0, str(workspace / "src"))

    files = sorted(SCENARIOS_DIR.glob("*.json"))
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for path in files:
            scenario = json.loads(path.read_text())
            account_file = Path(tmp) / path.name
            account_file.write_text(json.dumps(scenario["account"]))
            problems = check_cli(scenario, account_file, workspace)
            try:
                problems += check_engine(scenario)
            except Exception as exc:  # report it as a failed scenario, keep going
                problems.append(f"engine raised {type(exc).__name__}: {exc}")
            if problems:
                failures += 1
                print(f"FAIL {path.stem}: {scenario['description']}")
                for problem in problems:
                    print(f"     - {problem}")
            else:
                print(f"ok   {path.stem}")
    print(f"\n{len(files) - failures}/{len(files)} scenarios match the hand-checked results.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
