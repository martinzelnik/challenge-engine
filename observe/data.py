"""Read-only views of what the agent pipeline did, for the dashboard.

Everything here reads files and git; nothing calls a model or changes the repo.
Sources:
- feature_list.json: the features and whether they pass
- logs/agent-runs.jsonl: one record per pipeline run (cost, turns, denials, summaries)
- run logs: the pipeline's live terminal output, one file per run
- git: each feature branch's test(...) and feat(...) commits
- gh (optional): each branch's PR and review state
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from harness.pipeline import branch_name

ROOT = Path(__file__).resolve().parent.parent
RUN_LEDGER = Path("logs") / "agent-runs.jsonl"
RUN_LOG_DIR = Path("logs") / "runs"

# --- run ledger ---------------------------------------------------------------


def load_runs(root: Path, extra_ledgers: list[str] = ()) -> list[dict]:
    """Every run record, newest first, from the ledger in this checkout plus `extra_ledgers`.

    The pipeline commits its ledger line on the feature branch, so runs whose
    branch isn't merged yet are only in that branch's copy of the ledger; pass
    those copies as `extra_ledgers`. Duplicates (same time and feature) count once
    and lines that aren't JSON are skipped.
    """
    path = root / RUN_LEDGER
    texts = [path.read_text() if path.exists() else "", *extra_ledgers]
    runs: dict[tuple[str, str], dict] = {}
    for text in texts:
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and "feature" in record:
                runs.setdefault((record.get("at", ""), record["feature"]), _with_totals(record))
    return sorted(runs.values(), key=lambda r: r.get("at", ""), reverse=True)


def _with_totals(record: dict) -> dict:
    roles = record.get("roles") or []
    record["total_cost_usd"] = round(sum(r.get("cost_usd") or 0 for r in roles), 4)
    record["total_turns"] = sum(r.get("turns") or 0 for r in roles)
    record["total_seconds"] = round(sum(r.get("seconds") or 0 for r in roles), 1)
    record["total_denials"] = sum(len(r.get("denied_tool_calls") or []) for r in roles)
    # Hosted runs log the session's `list_cost.amount`, which looks like whole cents
    # (the same API takes budgets in cents), so $29.00 is probably $0.29. Flag it
    # rather than guess, and keep it out of the spend totals.
    record["cost_unverified"] = record.get("runner") == "managed-agents"
    return record


def denial_summary(runs: list[dict]) -> list[dict]:
    """Denied tool calls grouped by role, tool and reason, most frequent first.

    A reason that shows up in every run is policy friction worth fixing in the
    role's prompt, not a one-off.
    """
    counts: Counter[tuple[str, str, str]] = Counter()
    seen_in: dict[tuple[str, str, str], set[str]] = {}
    for run in runs:
        for role in run.get("roles") or []:
            for denial in role.get("denied_tool_calls") or []:
                key = (role.get("role", "?"), denial.get("tool", "?"), denial.get("reason", ""))
                counts[key] += 1
                seen_in.setdefault(key, set()).add(run.get("at", ""))
    return [
        {"role": k[0], "tool": k[1], "reason": k[2], "count": n, "runs": len(seen_in[k])}
        for k, n in counts.most_common()
    ]


# --- live run logs ------------------------------------------------------------

_FIRST = re.compile(r"^(?P<feature>F-\d+) .* -> branch (?P<branch>\S+)$")
_PHASE = re.compile(r"^(?P<step>\d/\d) (?P<role>[\w-]+)")
_DENIED = re.compile(
    r"^\s+\[(?P<role>[\w-]+)\] (?:denied|DENIED) (?P<tool>\w+)(?P<colon>:?) (?P<rest>.*)$"
)
_TARGET_REASON = re.compile(r"^(?P<target>.*?): (?P<reason>[A-Z`].*)$")
_TOOL = re.compile(r"^\s+\[(?P<role>[\w-]+)\] (?P<tool>\w+)(?: (?P<target>.*))?$")
_WATCH = re.compile(r"^\s+watch:\s+(?P<url>\S+)")
_PR = re.compile(r"^PR opened: (?P<url>\S+)")
_EXIT = re.compile(r"^EXIT (?P<code>-?\d+)$")


def parse_log_line(line: str) -> dict:
    """Classify one line of the pipeline's terminal output."""
    if m := _DENIED.match(line):
        # local mode prints "denied Tool: reason"; hosted prints "DENIED Tool target: reason"
        # (policy reasons start with a capital, which tells them apart from the target)
        target, reason = "", m["rest"]
        if not m["colon"] and (tr := _TARGET_REASON.match(m["rest"])):
            target, reason = tr["target"], tr["reason"]
        return {
            "kind": "denied",
            "role": m["role"],
            "tool": m["tool"],
            "target": target,
            "reason": reason,
        }
    if m := _WATCH.match(line):
        return {"kind": "link", "label": "Console", "url": m["url"]}
    if m := _TOOL.match(line):
        return {"kind": "tool", "role": m["role"], "tool": m["tool"], "target": m["target"] or ""}
    if m := _PR.match(line):
        return {"kind": "link", "label": "PR", "url": m["url"]}
    if m := _FIRST.match(line):
        return {"kind": "start", "feature": m["feature"], "branch": m["branch"]}
    if m := _PHASE.match(line):
        return {"kind": "phase", "step": m["step"], "role": m["role"]}
    if m := _EXIT.match(line):
        return {"kind": "exit", "code": int(m["code"])}
    if line.startswith(("Tests committed as", "Done:")):
        return {"kind": "milestone"}
    if line.startswith(("Pipeline stopped", "Pipeline didn't start", "Push failed")) or (
        "checks failed" in line
    ):
        return {"kind": "problem"}
    return {"kind": "info"}


def summarize_log(text: str) -> dict:
    """What a run log says so far: feature, branch, phase, counts and outcome."""
    summary: dict[str, Any] = {
        "feature": None,
        "branch": None,
        "phase": None,
        "tool_calls": 0,
        "denials": 0,
        "links": [],
        "outcome": "running",
        "last_line": "",
    }
    for line in text.splitlines():
        if not line.strip():
            continue
        summary["last_line"] = line.strip()
        event = parse_log_line(line)
        kind = event["kind"]
        if kind == "start":
            summary["feature"], summary["branch"] = event["feature"], event["branch"]
        elif kind == "phase":
            summary["phase"] = event["role"]
        elif kind == "tool":
            summary["tool_calls"] += 1
        elif kind == "denied":
            summary["denials"] += 1
        elif kind == "link":
            summary["links"].append({"label": event["label"], "url": event["url"]})
        elif kind == "exit":
            summary["outcome"] = "passed" if event["code"] == 0 else "failed"
        if line.startswith("Done:"):
            summary["outcome"] = "passed"
        elif line.startswith(("Pipeline stopped", "Pipeline didn't start")):
            summary["outcome"] = "failed"
    return summary


def safe_log_path(dirs: list[Path], name: str) -> Path | None:
    """The log file called `name` in one of `dirs`, or None. Never leaves those dirs."""
    if not re.fullmatch(r"[\w.-]+\.log", name):
        return None
    for d in dirs:
        path = (d / name).resolve()
        if path.parent == d.resolve() and path.is_file():
            return path
    return None


def list_logs(dirs: list[Path]) -> list[dict]:
    """Run logs in `dirs`, newest first, each with a summary of what it says."""
    seen, logs = set(), []
    for d in dirs:
        if not d.is_dir():
            continue
        for path in d.glob("*.log"):
            if path.name in seen:
                continue
            seen.add(path.name)
            text = path.read_text(errors="replace")
            logs.append(
                {
                    "name": path.name,
                    "dir": str(d),
                    "size": path.stat().st_size,
                    "modified": path.stat().st_mtime,
                    **summarize_log(text),
                }
            )
    return sorted(logs, key=lambda log: log["modified"], reverse=True)


def read_from(path: Path, offset: int, limit: int = 256_000) -> tuple[str, int]:
    """Text appended to `path` since byte `offset`, and the new offset (for tailing)."""
    size = path.stat().st_size
    if offset > size:  # the file was replaced; start over
        offset = 0
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(limit)
    # don't split a UTF-8 character or a line across two reads
    cut = chunk.rfind(b"\n") + 1 if len(chunk) == limit else len(chunk)
    return chunk[:cut].decode("utf-8", errors="replace"), offset + cut


# --- transcripts --------------------------------------------------------------


def find_transcript(session_id: str, projects_dir: Path) -> Path | None:
    """Claude Code keeps each session's transcript as <projects>/<cwd slug>/<id>.jsonl."""
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id) or not projects_dir.is_dir():
        return None
    return next(projects_dir.glob(f"*/{session_id}.jsonl"), None)


def parse_transcript(text: str, limit: int = 2000) -> list[dict]:
    """A transcript as a flat list of text, tool calls and tool results."""
    entries: list[dict] = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = item.get("message") if isinstance(item, dict) else None
        if not isinstance(message, dict):
            continue
        role, content = message.get("role", item.get("type", "?")), message.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and block.get("text", "").strip():
                entries.append({"kind": "text", "role": role, "text": block["text"][:limit]})
            elif kind == "tool_use":
                entries.append(
                    {
                        "kind": "tool_use",
                        "role": role,
                        "tool": block.get("name", "?"),
                        "input": json.dumps(block.get("input"), ensure_ascii=False)[:limit],
                    }
                )
            elif kind == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                entries.append(
                    {
                        "kind": "tool_result",
                        "role": role,
                        "error": bool(block.get("is_error")),
                        "text": str(body or "")[:limit],
                    }
                )
    return entries


# --- git and GitHub -----------------------------------------------------------


def git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout


def github_repo(remote_url: str | None) -> str | None:
    """owner/name from an origin URL (https, ssh or a proxy path ending in owner/name)."""
    if not remote_url:
        return None
    m = re.search(r"[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", remote_url.strip())
    return f"{m[1]}/{m[2]}" if m else None


def main_ref(root: Path) -> str | None:
    """`origin/main` (what GitHub has merged, as of the last fetch), else local `main`."""
    return next((b for b in ("origin/main", "main") if git(root, "rev-parse", "--verify", b)), None)


def branch_commits(root: Path, branch: str) -> tuple[str | None, list[dict]]:
    """The ref the branch was found at (local first, then origin) and its commits since main."""
    base = main_ref(root)
    for ref in (branch, f"origin/{branch}"):
        if git(root, "rev-parse", "--verify", "--quiet", ref) is None:
            continue
        span = f"{base}..{ref}" if base else ref
        out = git(root, "log", "--no-merges", "--format=%h%x09%cI%x09%s", span, "-n", "30") or ""
        commits = []
        for line in out.splitlines():
            sha, at, subject = (line.split("\t", 2) + ["", ""])[:3]
            commits.append({"sha": sha, "at": at, "subject": subject})
        return ref, commits
    return None, []


def pull_requests(root: Path) -> list[dict] | None:
    """Recent PRs via the gh CLI, or None when gh isn't installed or signed in."""
    if shutil.which("gh") is None:
        return None
    try:
        out = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--limit",
                "100",
                "--json",
                "number,title,headRefName,state,url,reviewDecision,isDraft,updatedAt",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        return json.loads(out.stdout)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None


# --- the feature board --------------------------------------------------------

STAGES = ("tests", "code", "pr", "review", "merged")


def feature_board(
    features: list[dict],
    runs: list[dict],
    branches: dict[str, tuple[str | None, list[dict]]],
    prs: list[dict] | None,
    merged: set[str] | None = None,
) -> list[dict]:
    """One row per feature: how far it got through the pipeline and what it cost.

    `merged` is the ids that pass on main; it defaults to the ones that pass in
    this checkout. A merged feature counts as done even if its old branch is left.

    Stages: failing tests committed -> code committed -> PR open -> reviewer
    approved -> merged. `branches` maps branch name to (ref, commits).
    """
    pr_by_branch: dict[str, dict] = {}
    for pr in sorted(prs or [], key=lambda p: p.get("number", 0)):
        pr_by_branch[pr.get("headRefName", "")] = pr  # the newest PR per branch wins

    rows = []
    for feature in features:
        fid = feature["id"]
        branch = branch_name(feature)
        ref, commits = branches.get(branch, (None, []))
        subjects = [c["subject"] for c in commits]
        feature_runs = [r for r in runs if r.get("feature") == fid]
        pr = pr_by_branch.get(branch)
        pr_merged = bool(pr and pr.get("state") == "MERGED")
        done = {
            "tests": pr_merged or any(s.startswith(f"test({fid})") for s in subjects),
            "code": pr_merged or any(s.startswith(f"feat({fid})") for s in subjects),
            "pr": pr is not None,
            "review": pr_merged or bool(pr and pr.get("reviewDecision") == "APPROVED"),
            "merged": pr_merged,
        }
        on_main = fid in merged if merged is not None else bool(feature.get("passes"))
        if on_main:
            done = dict.fromkeys(STAGES, True)
        last_run = feature_runs[0] if feature_runs else None
        rows.append(
            {
                "id": fid,
                "title": feature["title"],
                "passes": on_main or pr_merged,
                "acceptance": feature.get("acceptance", []),
                "branch": branch,
                "branch_ref": ref,
                "commits": commits,
                "stages": done,
                "changes_requested": bool(pr and pr.get("reviewDecision") == "CHANGES_REQUESTED"),
                "pr": pr,
                "runs": len(feature_runs),
                "last_outcome": last_run.get("outcome") if last_run else None,
                "last_error": last_run.get("error") if last_run else None,
                "cost_usd": round(
                    sum(r["total_cost_usd"] for r in feature_runs if not r["cost_unverified"]), 4
                ),
                "denials": sum(r["total_denials"] for r in feature_runs),
            }
        )
    return rows


def overview(runs: list[dict], board: list[dict]) -> dict:
    """Headline numbers. Spend counts only runs whose cost unit is known (see _with_totals)."""
    priced = [r for r in runs if not r["cost_unverified"]]
    total = sum(r["total_cost_usd"] for r in priced)
    passed = [r for r in priced if r.get("outcome") == "passed"]
    return {
        "runs_unpriced": len(runs) - len(priced),
        "features": len(board),
        "features_passing": sum(1 for f in board if f["passes"]),
        "runs": len(runs),
        "runs_passed": sum(1 for r in runs if r.get("outcome") == "passed"),
        "total_cost_usd": round(total, 4),
        "avg_cost_per_passed_run": round(total / len(passed), 4) if passed else None,
        "denials": sum(r["total_denials"] for r in runs),
        "denials_per_run": round(sum(r["total_denials"] for r in runs) / len(runs), 1)
        if runs
        else None,
    }
