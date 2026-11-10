"""What each agent role may do, enforced on every tool call.

`check()` runs as a PreToolUse hook, so it applies to every call the agent
makes, including ones the permission settings would otherwise allow. It
returns a reason to deny the call, or None to let it through. Pure function,
no SDK import, so it's easy to test.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_TOOLS = ("Read", "Grep", "Glob", "Write", "Edit", "MultiEdit", "Bash", "TodoWrite")
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_METACHARACTERS = set(";&|`$<>\n\\")
SEARCHABLE = ("src/", "tests/", "docs/")


@dataclass(frozen=True)
class Role:
    name: str
    writable: tuple[str, ...]  # path prefixes, relative to the repo root
    commands: tuple[str, ...]  # allowed executables (first word of a Bash command)
    prompt_file: str


TEST_WRITER = Role(
    name="test-writer",
    writable=("tests/",),
    commands=(".venv/bin/pytest",),
    prompt_file=".claude/agents/test-writer.md",
)

IMPLEMENTER = Role(
    name="implementer",
    writable=("src/challenge_engine/",),
    commands=(".venv/bin/pytest", ".venv/bin/ruff"),
    prompt_file=".claude/agents/implementer.md",
)


def _relative(path: str, root: Path) -> str | None:
    """Repo-relative POSIX path, or None if it points outside the repo."""
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _is_eval_path(rel: str) -> bool:
    return rel == "evals" or rel.startswith("evals/") or "hidden_tests" in rel


def check(role: Role, tool: str, tool_input: dict[str, Any], root: Path) -> str | None:
    if tool not in ALLOWED_TOOLS:
        return f"The {role.name} can't use {tool}."

    if tool in WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        rel = _relative(path, root)
        if rel is None:
            return f"{path} is outside the repository."
        if not any(rel.startswith(prefix) for prefix in role.writable):
            return (
                f"The {role.name} may only edit files under {', '.join(role.writable)}; "
                f"{rel} is off limits."
            )
        return None

    if tool == "Read":
        rel = _relative(tool_input.get("file_path", ""), root)
        if rel is None:
            return "Reading files outside the repository isn't allowed."
        if _is_eval_path(rel):
            return "Files under evals/ are off limits: they measure this pipeline."
        return None

    if tool in ("Grep", "Glob"):
        path = tool_input.get("path")
        if not path:
            return f"Pass a `path` to {tool} (one of {', '.join(SEARCHABLE)})."
        rel = _relative(path, root)
        if rel is None or not any((rel + "/").startswith(p) for p in SEARCHABLE):
            return f"{tool} may only search {', '.join(SEARCHABLE)}."
        return None

    if tool == "Bash":
        command = tool_input.get("command", "")
        if any(ch in SHELL_METACHARACTERS for ch in command):
            return "Run one plain command: no pipes, chaining, redirects or substitutions."
        try:
            words = shlex.split(command)
        except ValueError:
            return "Couldn't parse that command."
        if not words or words[0] not in role.commands:
            return f"The {role.name} may only run: {', '.join(role.commands)}."
        if any(_is_eval_path(w.lstrip("./")) or ".." in w for w in words[1:]):
            return "Commands may not touch evals/ or paths outside the repository."
        return None

    return None  # TodoWrite
