"""Deterministic steps of the agent pipeline: git, checks, bookkeeping.

Everything here is plain Python with no model calls, so the orchestrator's
decisions (which feature, whether tests are red or green, what gets
committed) don't depend on what an agent says.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

FEATURE_LIST = "feature_list.json"
PROGRESS = "progress.md"


class PipelineError(Exception):
    """A step failed in a way the pipeline shouldn't paper over."""


# --- features ---------------------------------------------------------------


def load_features(root: Path) -> dict:
    return json.loads((root / FEATURE_LIST).read_text())


def pick_feature(data: dict, wanted: str | None = None) -> dict:
    """The requested feature, or the first one that doesn't pass yet."""
    features = data["features"]
    if wanted:
        for feature in features:
            if feature["id"] == wanted:
                if feature["passes"]:
                    raise PipelineError(f"{wanted} already passes.")
                return feature
        raise PipelineError(f"No feature {wanted} in {FEATURE_LIST}.")
    for feature in features:
        if not feature["passes"]:
            return feature
    raise PipelineError("Every feature already passes.")


def mark_passing(root: Path, feature_id: str) -> None:
    """Flip one feature's `passes` to true and touch nothing else."""
    data = load_features(root)
    for feature in data["features"]:
        if feature["id"] == feature_id:
            feature["passes"] = True
            break
    else:
        raise PipelineError(f"No feature {feature_id}.")
    (root / FEATURE_LIST).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def branch_name(feature: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", feature["title"].lower()).strip("-")
    slug = re.sub(r"-r\d+$", "", slug)  # drop the "(R1)" spec reference
    return f"feat/{feature['id']}-{slug}"[:60].rstrip("-")


# --- progress log -----------------------------------------------------------


@dataclass
class RoleStats:
    role: str
    model: str
    cost_usd: float | None
    turns: int | None
    seconds: float
    attempts: int = 1


def progress_entry(feature: dict, stats: list[RoleStats], tests: str, today: date) -> str:
    agents = "; ".join(
        f"{s.role} ({s.model}, {s.turns or '?'} turns, "
        f"${s.cost_usd or 0:.2f}, {s.seconds:.0f}s"
        + (f", {s.attempts} attempts" if s.attempts > 1 else "")
        + ")"
        for s in stats
    )
    return (
        f"## {today.isoformat()} — {feature['id']} {feature['title']}\n"
        f"- Agent/model: agent pipeline: {agents}\n"
        f"- Done: test-writer wrote failing tests from the spec; implementer made them pass.\n"
        f"- Tests: {tests}, lint clean: yes\n"
        f"- Problems / surprises: see the agents' summaries in the run log "
        f"(logs/agent-runs.jsonl).\n"
        f"- Next: review the PR\n"
    )


def pr_body(
    feature: dict,
    roles: list[dict],
    tests: str,
    commits: list[str],
    review_rounds: list[dict] | None = None,
) -> str:
    """PR description: what was built, by which agents, at what cost.

    `review_rounds` ({round, commit, tests}) lists fixes made after the reviewer
    requested changes, so the description stays true after the first push.
    """
    rounds = [f"| {r['round']} | {r['commit']} | {r['tests']} |" for r in review_rounds or []]
    rounds_section = (
        [
            "## Review rounds",
            "The reviewer requested changes; the implementer's session fixed them.",
            "",
            "| Round | Fix commit | Tests |",
            "|---|---|---|",
            *rounds,
            "",
        ]
        if rounds
        else []
    )
    rows = [
        f"| {r['role']} | `{r['model']}` | {r.get('turns') or '?'} | "
        f"${r.get('cost_usd') or 0:.2f} | {r.get('seconds', 0):.0f}s | "
        f"{max(r.get('attempts', 1) - 1, 0)} | {len(r.get('denied_tool_calls') or [])} |"
        for r in roles
    ]
    summaries = [
        f"<details><summary>{r['role']}</summary>\n\n"
        f"{(r.get('summary') or '').strip()}\n\n</details>"
        for r in roles
    ]
    return "\n".join(
        [
            f"Implements **{feature['id']}: {feature['title']}**, written by the agent "
            "pipeline (`harness/run_feature.py`).",
            "",
            "## Acceptance criteria",
            *[f"- {c}" for c in feature["acceptance"]],
            "",
            "## Agents",
            "| Role | Model | Turns | Cost | Time | Retries | Denied tool calls |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
            f"Tests: {tests}. Commits: {', '.join(commits)}.",
            "",
            *rounds_section,
            "## Agent summaries",
            *summaries,
            "",
            "The reviewer agent reviews this PR in CI. A human merges.",
            "",
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        ]
    )


def add_progress_entry(root: Path, entry: str) -> None:
    """Insert the entry as the newest one, right after the `---` separator."""
    path = root / PROGRESS
    text = path.read_text()
    marker = "\n---\n"
    if marker not in text:
        raise PipelineError(f"{PROGRESS} has no '---' separator before the entries.")
    head, tail = text.split(marker, 1)
    if not tail.startswith("\n"):
        tail = "\n" + tail
    path.write_text(f"{head}{marker}\n{entry}{tail}")


# --- git and checks ---------------------------------------------------------


def run(cmd: list[str], root: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise PipelineError(f"`{' '.join(cmd)}` failed:\n{result.stdout}{result.stderr}")
    return result


def changed_paths(root: Path) -> list[str]:
    """Paths with uncommitted changes, including untracked files."""
    out = run(["git", "status", "--porcelain", "--untracked-files=all"], root).stdout
    return sorted(line[3:].split(" -> ")[-1] for line in out.splitlines() if line.strip())


def require_clean_tree(root: Path) -> None:
    if changed_paths(root):
        raise PipelineError("Working tree has uncommitted changes. Commit or stash them first.")


def assert_only_under(paths: list[str], prefixes: tuple[str, ...], role: str) -> None:
    stray = [p for p in paths if not p.startswith(prefixes)]
    if stray:
        raise PipelineError(f"The {role} changed files outside {prefixes}: {stray}")


def run_tests(root: Path, paths: list[str] | None = None) -> tuple[bool, str]:
    result = run([".venv/bin/pytest", "-q", *(paths or [])], root, check=False)
    return result.returncode == 0, (result.stdout + result.stderr)


def run_checks(root: Path) -> tuple[bool, str]:
    """Full suite, lint and format check: what CI runs."""
    outputs, ok = [], True
    for cmd in (
        [".venv/bin/pytest", "-q"],
        [".venv/bin/ruff", "check", "."],
        [".venv/bin/ruff", "format", "--check", "."],
    ):
        result = run(cmd, root, check=False)
        ok &= result.returncode == 0
        outputs.append(f"$ {' '.join(cmd)}\n{result.stdout}{result.stderr}")
    return ok, "\n".join(outputs)


def summarize_pytest(output: str) -> str:
    """The '22 passed in 0.02s' line, or a fallback."""
    for line in reversed(output.splitlines()):
        if re.search(r"\d+ (passed|failed)", line):
            return line.strip(" =")
    return "see run log"


def commit(root: Path, paths: list[str], message: str) -> str:
    run(["git", "add", "--", *paths], root)
    run(["git", "commit", "-q", "-m", message], root)
    return run(["git", "rev-parse", "--short", "HEAD"], root).stdout.strip()
