"""Run the agent pipeline for one feature: test-writer, then implementer.

Usage:
    .venv/bin/python -m harness.run_feature                # next feature that doesn't pass
    .venv/bin/python -m harness.run_feature F-003 --budget 2

What it does:
1. Creates a branch for the feature (the working tree must be clean).
2. test-writer agent writes tests from the spec. The pipeline checks they only
   touch tests/, are lint-clean and FAIL, then commits them.
3. implementer agent writes the code. The pipeline checks it only touched
   src/challenge_engine/ and that tests, lint and format pass (feeding failures
   back up to --retries times), marks the feature passing, adds a progress
   entry and commits.
4. Logs each agent's model, cost, turns, time and denied tool calls to
   logs/agent-runs.jsonl.

Pushing and opening the PR stay with a human; the reviewer agent runs in CI.
Each role's limits are enforced in code by harness/policy.py on every tool call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from . import pipeline, policy
from .pipeline import PipelineError, RoleStats

ROOT = Path(__file__).resolve().parent.parent
RUN_LOG = ROOT / "logs" / "agent-runs.jsonl"
DEFAULT_MODEL = "claude-opus-5-5"


def role_prompt(role: policy.Role) -> str:
    """The role's instructions: its .claude/agents/*.md file without the frontmatter."""
    text = (ROOT / role.prompt_file).read_text()
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


def make_options(
    role: policy.Role, model: str, max_turns: int, budget: float, denials: list[dict]
) -> ClaudeAgentOptions:
    async def guard(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict:
        tool = input_data.get("tool_name", "")
        reason = policy.check(role, tool, input_data.get("tool_input") or {}, ROOT)
        if reason is None:
            return {}
        denials.append({"tool": tool, "reason": reason})
        print(f"  [{role.name}] denied {tool}: {reason}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return ClaudeAgentOptions(
        cwd=str(ROOT),
        model=model,
        max_turns=max_turns,
        max_budget_usd=budget,
        system_prompt={"type": "preset", "preset": "claude_code", "append": role_prompt(role)},
        setting_sources=["project"],  # loads CLAUDE.md/AGENTS.md and .claude/settings.json denies
        allowed_tools=list(policy.ALLOWED_TOOLS),
        permission_mode="dontAsk",  # anything not allowed is refused, never prompted
        hooks={"PreToolUse": [HookMatcher(hooks=[guard])]},
    )


async def ask(client: ClaudeSDKClient, role: str, prompt: str) -> tuple[ResultMessage | None, str]:
    """Send one message and wait for the agent to finish its turn."""
    result, texts = None, []
    try:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        target = block.input.get("file_path") or block.input.get("command") or ""
                        print(f"  [{role}] {block.name} {str(target).replace(str(ROOT) + '/', '')}")
                    elif isinstance(block, TextBlock):
                        texts.append(block.text)
            elif isinstance(message, ResultMessage):
                result = message
    except ClaudeSDKError as e:
        # e.g. the budget cap was hit; stop cleanly so the run still gets logged
        raise PipelineError(f"The {role} stopped: {e}") from e
    if result is not None and result.is_error:
        raise PipelineError(f"The {role} stopped with an error: {result.subtype}")
    return result, "\n".join(texts)


def tail(text: str, limit: int = 6000) -> str:
    return text if len(text) <= limit else "…\n" + text[-limit:]


async def write_tests(feature: dict, args: argparse.Namespace, record: dict) -> list[str]:
    role, denials = policy.TEST_WRITER, []
    criteria = "\n".join(f"- {c}" for c in feature["acceptance"])
    prompt = (
        f"Feature {feature['id']}: {feature['title']}\n\nAcceptance criteria:\n{criteria}\n\n"
        "Write the failing tests for this feature now, following your instructions."
    )
    started = time.monotonic()
    async with ClaudeSDKClient(
        options=make_options(role, args.model, 40, args.budget, denials)
    ) as client:
        result, summary = await ask(client, role.name, prompt)
        for attempt in range(1, args.retries + 2):
            changed = pipeline.changed_paths(ROOT)
            if not changed:
                raise PipelineError("The test-writer didn't write any tests.")
            pipeline.assert_only_under(changed, role.writable, role.name)
            tests = [p for p in changed if p.endswith(".py")]
            pipeline.run([".venv/bin/ruff", "format", *tests], ROOT, check=False)
            lint = pipeline.run([".venv/bin/ruff", "check", *tests], ROOT, check=False)
            passed, output = pipeline.run_tests(ROOT, tests)
            summary_line = pipeline.summarize_pytest(output)
            if lint.returncode == 0 and not passed and " failed" in summary_line:
                break  # red, as it should be
            if attempt > args.retries:
                raise PipelineError(f"Tests aren't a clean red run: {summary_line}")
            problem = (
                f"ruff reports problems:\n{lint.stdout}"
                if lint.returncode
                else "Your tests already pass, so they don't test the new feature."
                if passed
                else f"The tests don't fail cleanly (collection errors?):\n{tail(output)}"
            )
            result, summary = await ask(client, role.name, f"{problem}\nFix the tests.")
    record["roles"].append(
        _role_record(role.name, args.model, result, started, attempt, denials, summary)
    )
    sha = pipeline.commit(
        ROOT,
        changed,
        _message(f"test({feature['id']}): failing tests for {feature['title']}", args),
    )
    record["commits"].append(sha)
    print(f"Tests committed as {sha}: {summary_line}")
    return tests


async def implement(feature: dict, tests: list[str], args: argparse.Namespace, record: dict) -> str:
    role, denials = policy.IMPLEMENTER, []
    prompt = (
        f"Feature {feature['id']}: {feature['title']}\n\n"
        f"The test-writer's new tests, currently failing: {', '.join(tests)}\n\n"
        "Implement the feature so the whole suite passes, following your instructions."
    )
    started = time.monotonic()
    async with ClaudeSDKClient(
        options=make_options(role, args.model, 60, args.budget, denials)
    ) as client:
        result, summary = await ask(client, role.name, prompt)
        for attempt in range(1, args.retries + 2):
            changed = pipeline.changed_paths(ROOT)
            pipeline.assert_only_under(changed, role.writable, role.name)
            ok, output = pipeline.run_checks(ROOT)
            if ok:
                break
            if attempt > args.retries:
                raise PipelineError(f"Checks still fail after {args.retries} retries.")
            print(f"  checks failed, retry {attempt}/{args.retries}")
            result, summary = await ask(
                client,
                role.name,
                f"The checks fail:\n\n{tail(output)}\n\nFix the code in src/ only.",
            )
    record["roles"].append(
        _role_record(role.name, args.model, result, started, attempt, denials, summary)
    )
    return pipeline.summarize_pytest(output)


def _role_record(role, model, result, started, attempts, denials, summary) -> dict:
    return {
        "role": role,
        "model": model,
        "cost_usd": result.total_cost_usd if result else None,  # cumulative for the session
        "turns": result.num_turns if result else None,
        "session_id": result.session_id
        if result
        else None,  # full transcript is kept under this id
        "seconds": round(time.monotonic() - started, 1),
        "attempts": attempts,
        "denied_tool_calls": denials,
        "summary": summary[-2000:],
    }


def _message(subject: str, args: argparse.Namespace) -> str:
    return (
        f"{subject}\n\nWritten by the agent pipeline (harness/run_feature.py).\n\n"
        f"Co-Authored-By: Claude ({args.model}) <noreply@anthropic.com>"
    )


def _write_log(record: dict) -> None:
    RUN_LOG.parent.mkdir(exist_ok=True)
    with RUN_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")


async def main_async(args: argparse.Namespace) -> int:
    pipeline.require_clean_tree(ROOT)
    feature = pipeline.pick_feature(pipeline.load_features(ROOT), args.feature)
    branch = pipeline.branch_name(feature)
    record: dict = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "feature": feature["id"],
        "branch": branch,
        "model": args.model,
        "outcome": "failed",
        "roles": [],
        "commits": [],
    }
    print(f"{feature['id']} {feature['title']} -> branch {branch}")
    try:
        pipeline.run(["git", "switch", "-c", branch], ROOT)
        print("1/2 test-writer")
        tests = await write_tests(feature, args, record)
        print("2/2 implementer")
        test_summary = await implement(feature, tests, args, record)

        stats = [
            RoleStats(r["role"], r["model"], r["cost_usd"], r["turns"], r["seconds"], r["attempts"])
            for r in record["roles"]
        ]
        pipeline.mark_passing(ROOT, feature["id"])
        pipeline.add_progress_entry(
            ROOT, pipeline.progress_entry(feature, stats, test_summary, date.today())
        )
        record["outcome"] = "passed"
        _write_log(record)
        changed = pipeline.changed_paths(ROOT)
        sha = pipeline.commit(
            ROOT, changed, _message(f"feat({feature['id']}): {feature['title']}", args)
        )
        record["commits"].append(sha)
    except PipelineError as e:
        record["error"] = str(e)
        _write_log(record)
        print(f"\nPipeline stopped: {e}\nBranch {branch} is left as it is for a human to look at.")
        return 1

    total = sum(r["cost_usd"] or 0 for r in record["roles"])
    print(
        f"\nDone: {feature['id']} implemented on {branch} ({test_summary}, ${total:.2f}).\n"
        f"Next: push the branch and open a PR; the reviewer agent reviews it in CI:\n"
        f"  git push -u origin {branch}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("feature", nargs="?", help="Feature id, e.g. F-003 (default: next one)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget", type=float, default=3.0, help="Max USD per agent")
    parser.add_argument("--retries", type=int, default=2, help="Feedback rounds per agent")
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except PipelineError as e:
        print(f"Pipeline didn't start: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
