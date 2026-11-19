"""Answer the review comments on an agent-pipeline PR: fix the valid ones, say why not for the rest.

Usage:
    .venv/bin/python -m harness.address_comments 12               # fix, push, reply
    .venv/bin/python -m harness.address_comments 12 --no-push --plan-out plan.json
    .venv/bin/python -m harness.address_comments 12 --post-replies plan.json

What it does:
1. Collects the PR's comments that still need an answer (inline comments, review
   bodies and conversation comments from people with write access or the CI
   reviewer; see harness/review_comments.py). None: it stops before any model call.
2. Checks out the PR's feat/* branch, up to date with origin. It refuses PRs
   from forks, PRs that change harness files, and PRs that already had
   --max-rounds rounds of fixes (so the reviewer and responder can't loop forever).
3. The review-responder agent decides on each comment, fixes the valid ones in
   src/challenge_engine/ or tests/ and returns a decision with a reason for each.
4. The orchestrator checks the fix: only allowed paths changed, no test removed,
   no existing assertion removed, tests, lint and format green. Failures go back
   to the agent up to --retries times; after that the fix is thrown away.
5. Commits the fix with a progress.md entry, pushes the branch (feat/* only) and
   replies to every comment: "Fixed in <sha>", "Not changed: <why>", or "Tried,
   but the checks failed". The push re-runs CI and the reviewer.

--no-push stops after the commit and saves the replies to --plan-out, so that
CI can push and reply in separate steps whose tokens the agent never sees
(.github/workflows/address-comments.yml).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient

from . import pipeline, policy
from . import review_comments as rc
from .pipeline import PipelineError
from .run_feature import (
    DEFAULT_MODEL,
    ROOT,
    _message,
    _role_record,
    _write_log,
    ask,
    make_options,
    tail,
)


def gh(args: list[str]) -> str:
    return pipeline.run(["gh", *args], ROOT).stdout


def gh_json(args: list[str]) -> Any:
    return json.loads(gh(args) or "null")


def gh_list(path: str) -> list[dict]:
    """Every item of a paginated REST list."""
    pages = gh_json(["api", "--paginate", "--slurp", path]) or []
    return [item for page in pages for item in page]


def repo_name() -> str:
    return gh_json(["repo", "view", "--json", "nameWithOwner"])["nameWithOwner"]


def fetch_comments(repo: str, pr: int) -> list[rc.Comment]:
    return rc.parse_comments(
        gh_list(f"repos/{repo}/pulls/{pr}/comments"),
        gh_list(f"repos/{repo}/pulls/{pr}/reviews"),
        gh_list(f"repos/{repo}/issues/{pr}/comments"),
    )


def protected_prefixes() -> tuple[str, ...]:
    """The paths CI treats as harness files (scripts/check_boundaries.py)."""
    spec = importlib.util.spec_from_file_location(
        "check_boundaries", ROOT / "scripts" / "check_boundaries.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PROTECTED_PREFIXES


def checkout(branch: str, base: str) -> None:
    """Switch to the PR branch, exactly at origin's tip."""
    pipeline.run(["git", "fetch", "-q", "origin", branch, base], ROOT)
    local = pipeline.run(["git", "rev-parse", "--verify", "-q", branch], ROOT, check=False)
    if local.returncode == 0:
        pipeline.run(["git", "switch", "-q", branch], ROOT)
        pipeline.run(["git", "merge", "-q", "--ff-only", f"origin/{branch}"], ROOT)
    else:
        pipeline.run(["git", "switch", "-q", "-c", branch, "--track", f"origin/{branch}"], ROOT)
    head = pipeline.run(["git", "rev-parse", "HEAD"], ROOT).stdout.strip()
    remote = pipeline.run(["git", "rev-parse", f"origin/{branch}"], ROOT).stdout.strip()
    if head != remote:
        raise PipelineError(f"Local {branch} has commits that aren't on origin. Push or drop them.")


def collect_test_ids() -> set[str]:
    out = pipeline.run([".venv/bin/pytest", "--collect-only", "-q"], ROOT, check=False).stdout
    return rc.collected_ids(out)


def fix_problem(role: policy.Role, decisions: list[dict], before: set[str]) -> str | None:
    """Why the agent's work can't be committed yet, or None if it can."""
    changed = pipeline.changed_paths(ROOT)
    fixed = [d["id"] for d in decisions if d["action"] == "fixed"]
    if fixed and not changed:
        return f"You marked {', '.join(fixed)} fixed but changed no files."
    if changed and not fixed:
        return "You changed files but marked nothing fixed. Revert them or mark what they fix."
    if not changed:
        return None
    pipeline.assert_only_under(changed, role.writable, role.name)
    removed = rc.removed_assertions(
        pipeline.run(["git", "diff", "HEAD", "--", "tests"], ROOT).stdout
    )
    if removed:
        return (
            "You removed or changed existing assertions, which only a human may do:\n"
            + "\n".join(f"- {r}" for r in removed)
            + "\nRestore them. If a comment needs that change, mark it skipped and say so."
        )
    if missing := before - collect_test_ids():
        return f"These tests no longer exist; restore them: {', '.join(sorted(missing))}"
    ok, output = pipeline.run_checks(ROOT)
    return None if ok else f"The checks fail:\n\n{tail(output)}"


def discard_changes(role: policy.Role) -> None:
    pipeline.run(["git", "checkout", "HEAD", "--", *role.writable], ROOT)
    pipeline.run(["git", "clean", "-fdq", "--", *role.writable], ROOT)


async def respond(pr: int, args: argparse.Namespace) -> dict:
    """Run the responder on one PR. Returns the run record, with `plan` and `commit`."""
    pipeline.require_clean_tree(ROOT)
    info = gh_json(
        ["pr", "view", str(pr), "--json", "state,headRefName,baseRefName,isCrossRepository"]
    )
    branch, base = info["headRefName"], info["baseRefName"]
    if info["state"] != "OPEN" or info["isCrossRepository"] or not branch.startswith("feat/"):
        raise PipelineError(f"PR #{pr} isn't an open feat/* PR from this repository.")
    repo = repo_name()
    comments = fetch_comments(repo, pr)
    todo = rc.pending(comments)
    record: dict = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "kind": "address-comments",
        "pr": pr,
        "feature": rc.feature_id(branch),
        "branch": branch,
        "model": args.model,
        "outcome": "nothing-to-do",
        "roles": [],
        "commits": [],
        "plan": [],
        "commit": None,
    }
    if not todo:
        print(f"PR #{pr}: no review comments waiting for an answer.")
        return record

    print(f"PR #{pr}: {len(todo)} comment(s) to answer on {branch}")
    protected = protected_prefixes()  # read before the branch replaces the files
    checkout(branch, base)
    touched = pipeline.run(
        ["git", "diff", "--name-only", f"origin/{base}...HEAD"], ROOT
    ).stdout.split()
    # The branch then runs the same harness, agent prompts and settings as its
    # base, so a PR can't change the rules its own responder plays by.
    if harness := [p for p in touched if p.startswith(protected)]:
        raise PipelineError(f"PR #{pr} changes harness files ({harness[0]}, …); a human answers.")
    subjects = pipeline.run(
        ["git", "log", "--format=%s", f"origin/{base}..HEAD"], ROOT
    ).stdout.splitlines()
    if rc.rounds_so_far(subjects) >= args.max_rounds:
        record["outcome"] = "rounds-exhausted"
        print(f"Already {args.max_rounds} rounds of fixes on this PR; a human takes over.")
        return record

    role, denials = policy.REVIEW_RESPONDER, []
    before = collect_test_ids()
    schema = {"type": "json_schema", "schema": rc.DECISION_SCHEMA}
    started, failed = time.monotonic(), False
    async with ClaudeSDKClient(
        options=make_options(role, args.model, 60, args.budget, denials, schema)
    ) as client:
        result, summary = await ask(client, role.name, rc.build_prompt(pr, branch, todo))
        for attempt in range(1, args.retries + 2):
            raw = result.structured_output if result else None
            decisions = rc.normalize_decisions(raw, todo)
            problem = fix_problem(role, decisions, before)
            if problem is None:
                break
            if attempt > args.retries:
                failed = True
                record["error"] = f"Gave up after {args.retries} retries: {problem[:2000]}"
                break
            print(f"  fix not accepted, retry {attempt}/{args.retries}")
            result, summary = await ask(
                client, role.name, f"{problem}\n\nFix that, then give your decisions again."
            )
    record["roles"].append(
        _role_record(role.name, args.model, result, started, attempt, denials, summary)
    )

    commit = None
    if failed:
        discard_changes(role)
    elif pipeline.changed_paths(ROOT):
        _, output = pipeline.run_tests(ROOT)
        record["tests"] = pipeline.summarize_pytest(output)
    plan = rc.build_plan(todo, decisions, None)
    if not failed and any(d["action"] == "fixed" for d in decisions):
        planned = rc.build_plan(todo, decisions, "pending")  # for the progress entry
        pipeline.add_progress_entry(
            ROOT,
            rc.progress_entry(
                record["feature"], pr, planned, record["roles"][0], record["tests"], date.today()
            ),
        )
        record["outcome"] = "fixed"
        _write_log(record)  # committed with the fix, like the feature runs
        subject = f"fix({record['feature'] or 'review'}): {rc.ROUND_SUBJECT} on PR #{pr}"
        commit = pipeline.commit(ROOT, pipeline.changed_paths(ROOT), _message(subject, args))
        record["commits"].append(commit)
        plan = rc.build_plan(todo, decisions, commit)
    else:
        record["outcome"] = "failed" if failed else "replied-only"
        _write_log(record)
    record["commit"], record["plan"] = commit, plan
    fixed = sum(p["status"] == "fixed" for p in plan)
    print(f"  {fixed} fixed, {len(plan) - fixed} not" + (f", commit {commit}" if commit else ""))
    return record


def push(branch: str) -> None:
    if not branch.startswith("feat/"):
        raise PipelineError(f"Refusing to push {branch}: only feat/* branches are pushed.")
    pipeline.run(["git", "push", "origin", branch], ROOT)


def post_replies(pr: int, plan: list[dict], commit: str | None, model: str) -> None:
    repo = repo_name()
    for kind, comment_id, body in rc.replies(plan, commit, model):
        path = (
            f"repos/{repo}/pulls/{pr}/comments/{comment_id}/replies"
            if kind == "inline"
            else f"repos/{repo}/issues/{pr}/comments"
        )
        gh(["api", "-X", "POST", path, "-f", f"body={body}", "--silent"])
    print(f"  replied to {len(plan)} comment(s) on PR #{pr}")


def wait_for_review(sha: str, timeout: float = 1800) -> None:
    """Block until every "PR review" workflow run on `sha` has finished."""
    deadline = time.monotonic() + timeout
    print(f"  waiting for CI and the reviewer on {sha[:7]} (gh run watch to follow it)")
    while time.monotonic() < deadline:
        args = ["run", "list", "--workflow", "PR review", "--commit", sha, "--json", "status"]
        runs = gh_json(args) or []
        if runs and all(r["status"] == "completed" for r in runs):
            return
        time.sleep(20)
    raise PipelineError(f"Timed out waiting for the PR review workflow on {sha[:7]}.")


async def review_loop(pr: int, args: argparse.Namespace) -> None:
    """After the pipeline opens a PR: wait for the reviewer, answer, repeat.

    Stops when a round leaves nothing to fix or --max-rounds fix commits exist.
    Don't combine with the CI responder (address-comments.yml) on the same PR.
    """
    while True:
        wait_for_review(pipeline.run(["git", "rev-parse", "HEAD"], ROOT).stdout.strip())
        record = await respond_and_publish(pr, args)
        if not record["commit"]:
            print(f"  review loop done: {record['outcome']}")
            return


async def respond_and_publish(pr: int, args: argparse.Namespace) -> dict:
    """respond(), then push the fix and reply. The step run_feature chains after its PR."""
    record = await respond(pr, args)
    if record["plan"]:
        if record["commit"]:
            push(record["branch"])
        post_replies(pr, record["plan"], record["commit"], args.model)
    return record


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget", type=float, default=2.0, help="Max USD for the agent")
    parser.add_argument("--retries", type=int, default=2, help="Feedback rounds for the agent")
    parser.add_argument(
        "--max-rounds", type=int, default=3, help="Fix commits allowed per PR before a human"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr", type=int, help="Pull request number")
    add_arguments(parser)
    parser.add_argument("--no-push", dest="push", action="store_false", help="Commit only")
    parser.add_argument("--plan-out", type=Path, help="With --no-push: where to save replies")
    parser.add_argument("--post-replies", type=Path, help="Only post the replies in this plan")
    args = parser.parse_args()
    try:
        if args.post_replies:
            saved = json.loads(args.post_replies.read_text())
            if saved["plan"]:
                post_replies(args.pr, saved["plan"], saved["commit"], saved["model"])
            return 0
        if args.push:
            record = asyncio.run(respond_and_publish(args.pr, args))
        else:
            record = asyncio.run(respond(args.pr, args))
            if args.plan_out:
                args.plan_out.write_text(
                    json.dumps({k: record[k] for k in ("plan", "commit", "branch", "model")})
                )
    except PipelineError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    return 1 if record["outcome"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
