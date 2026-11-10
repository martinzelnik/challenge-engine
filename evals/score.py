"""Score a workspace against an eval task's hidden tests.

Usage:
    python evals/score.py F-002-max-daily-loss                 # score this repo
    python evals/score.py F-002-max-daily-loss --workspace ../run-42 --label sonnet

Runs the hidden tests against <workspace>/src, lints the workspace, and appends
one JSON line to evals/results/results.jsonl so results can be compared over time.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
TASKS_DIR = EVALS_DIR / "tasks"
RESULTS_FILE = EVALS_DIR / "results" / "results.jsonl"


def run_hidden_tests(task_dir: Path, workspace: Path) -> dict:
    hidden = task_dir / "hidden_tests"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copytree(hidden, tmp_path / "tests")
        (tmp_path / "pytest.ini").write_text("[pytest]\n")  # isolate from workspace config
        junit = tmp_path / "junit.xml"
        env = {**os.environ, "PYTHONPATH": str(workspace / "src")}
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={junit}",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if not junit.exists():
            return {"passed": 0, "total": 0, "error": proc.stdout[-2000:] + proc.stderr[-2000:]}
        suite = ET.parse(junit).getroot()
        suite = suite.find("testsuite") if suite.tag == "testsuites" else suite
        total = int(suite.get("tests", 0))
        not_passed = sum(int(suite.get(k, 0)) for k in ("failures", "errors", "skipped"))
        failed = [
            case.get("name")
            for case in suite.iter("testcase")
            if case.find("failure") is not None or case.find("error") is not None
        ]
        return {"passed": total - not_passed, "total": total, "failed": failed}


def lint_errors(workspace: Path) -> int | None:
    ruff = shutil.which("ruff") or str(Path(sys.executable).parent / "ruff")
    if not Path(ruff).exists():
        return None
    proc = subprocess.run(
        [ruff, "check", "src", "tests", "--output-format=json"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    try:
        return len(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return None


def git_commit(workspace: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=workspace, capture_output=True, text=True
    )
    return proc.stdout.strip() or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task", help="Task folder name under evals/tasks/")
    parser.add_argument("--workspace", type=Path, default=EVALS_DIR.parent)
    parser.add_argument("--label", default="manual", help="e.g. the model or harness version")
    parser.add_argument("--no-save", action="store_true", help="Don't append to results.jsonl")
    args = parser.parse_args()

    task_dir = TASKS_DIR / args.task
    if not (task_dir / "hidden_tests").is_dir():
        parser.error(f"no hidden tests for task {args.task!r} in {task_dir}")
    workspace = args.workspace.resolve()

    tests = run_hidden_tests(task_dir, workspace)
    result = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": args.task,
        "label": args.label,
        "commit": git_commit(workspace),
        "tests_passed": tests["passed"],
        "tests_total": tests["total"],
        "failed": tests.get("failed", []),
        "lint_errors": lint_errors(workspace),
    }
    if "error" in tests:
        result["error"] = tests["error"]

    print(json.dumps(result, indent=2))
    if not args.no_save:
        RESULTS_FILE.parent.mkdir(exist_ok=True)
        with RESULTS_FILE.open("a") as f:
            f.write(json.dumps(result) + "\n")
    return 0 if tests["total"] and tests["passed"] == tests["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
