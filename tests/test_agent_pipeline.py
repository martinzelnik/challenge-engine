"""Tests for the agent pipeline's role policy and deterministic steps (no model calls)."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from harness import pipeline, policy
from harness.policy import IMPLEMENTER, TEST_WRITER

ROOT = Path(__file__).resolve().parent.parent


def check(role, tool, **tool_input):
    return policy.check(role, tool, tool_input, ROOT)


# --- policy: writes ---------------------------------------------------------


def test_test_writer_may_write_tests():
    assert check(TEST_WRITER, "Write", file_path=str(ROOT / "tests/test_max_loss.py")) is None


@pytest.mark.parametrize(
    "path",
    ["src/challenge_engine/rules.py", "docs/spec.md", "feature_list.json", "progress.md"],
)
def test_test_writer_may_not_write_outside_tests(path):
    reason = check(TEST_WRITER, "Edit", file_path=str(ROOT / path))
    assert reason and "only edit files under tests/" in reason


def test_implementer_may_edit_engine_code():
    assert check(IMPLEMENTER, "Edit", file_path=str(ROOT / "src/challenge_engine/rules.py")) is None


@pytest.mark.parametrize("path", ["tests/test_max_loss.py", "harness/policy.py", "AGENTS.md"])
def test_implementer_may_not_edit_tests_or_harness(path):
    assert check(IMPLEMENTER, "Write", file_path=str(ROOT / path))


def test_paths_escaping_the_repo_are_denied():
    reason = check(IMPLEMENTER, "Write", file_path=str(ROOT / "src/../../outside.py"))
    assert reason and "outside the repository" in reason


def test_relative_paths_are_resolved_against_the_repo():
    assert check(TEST_WRITER, "Write", file_path="tests/test_x.py") is None
    assert check(TEST_WRITER, "Write", file_path="src/challenge_engine/x.py")


# --- policy: reads and searches ---------------------------------------------


def test_hidden_eval_tests_cannot_be_read():
    path = ROOT / "evals/tasks/F-002-max-daily-loss/hidden_tests/test_max_daily_loss.py"
    assert check(IMPLEMENTER, "Read", file_path=str(path))


def test_spec_and_source_can_be_read():
    assert check(IMPLEMENTER, "Read", file_path=str(ROOT / "docs/spec.md")) is None
    assert check(TEST_WRITER, "Read", file_path=str(ROOT / "src/challenge_engine/rules.py")) is None


def test_search_needs_an_allowed_path():
    assert check(IMPLEMENTER, "Grep", pattern="Decimal")  # no path: would include evals/
    assert check(IMPLEMENTER, "Grep", pattern="Decimal", path=str(ROOT))
    assert check(IMPLEMENTER, "Glob", pattern="**/*.py", path=str(ROOT / "evals"))
    assert check(IMPLEMENTER, "Grep", pattern="Decimal", path=str(ROOT / "src")) is None
    assert check(TEST_WRITER, "Glob", pattern="*.py", path="tests") is None


# --- policy: commands and tools ---------------------------------------------


def test_roles_may_run_their_own_commands():
    assert check(TEST_WRITER, "Bash", command=".venv/bin/pytest -q tests/test_x.py") is None
    assert check(IMPLEMENTER, "Bash", command=".venv/bin/ruff check .") is None


@pytest.mark.parametrize(
    "command",
    [
        "git commit -am done",
        "git push",
        ".venv/bin/ruff check .",  # test-writer has no ruff
        "cat evals/tasks/F-002-max-daily-loss/hidden_tests/test_max_daily_loss.py",
        ".venv/bin/pytest evals/tasks",
        ".venv/bin/pytest -q && git push",
        ".venv/bin/pytest -q; rm -rf src",
        ".venv/bin/pytest $(cat x)",
        "curl https://example.com",
    ],
)
def test_test_writer_commands_are_restricted(command):
    assert check(TEST_WRITER, "Bash", command=command)


@pytest.mark.parametrize("tool", ["WebFetch", "WebSearch", "Agent", "NotebookEdit"])
def test_unlisted_tools_are_denied(tool):
    assert check(IMPLEMENTER, tool)


# --- pipeline: features -----------------------------------------------------

FEATURES = {
    "_note": "x",
    "features": [
        {"id": "F-001", "title": "Models", "acceptance": ["a"], "passes": True},
        {"id": "F-003", "title": "Max loss rule (R2)", "acceptance": ["b"], "passes": False},
        {
            "id": "F-004",
            "title": "Minimum trading days rule (R4)",
            "acceptance": ["c"],
            "passes": False,
        },
    ],
}


def test_pick_feature_defaults_to_first_not_passing():
    assert pipeline.pick_feature(FEATURES)["id"] == "F-003"


def test_pick_feature_by_id():
    assert pipeline.pick_feature(FEATURES, "F-004")["id"] == "F-004"


@pytest.mark.parametrize("wanted", ["F-001", "F-999"])
def test_pick_feature_rejects_passing_or_unknown(wanted):
    with pytest.raises(pipeline.PipelineError):
        pipeline.pick_feature(FEATURES, wanted)


def test_branch_name_drops_spec_reference():
    assert pipeline.branch_name(FEATURES["features"][1]) == "feat/F-003-max-loss-rule"


def test_mark_passing_keeps_the_file_byte_for_byte_except_one_field(tmp_path):
    shutil.copy(ROOT / "feature_list.json", tmp_path / "feature_list.json")
    before = (tmp_path / "feature_list.json").read_text()
    target = pipeline.pick_feature(json.loads(before))["id"]

    pipeline.mark_passing(tmp_path, target)

    after = (tmp_path / "feature_list.json").read_text()
    changed = [
        (a, b) for a, b in zip(before.splitlines(), after.splitlines(), strict=True) if a != b
    ]
    assert changed == [('      "passes": false', '      "passes": true')]


# --- pipeline: progress log -------------------------------------------------


def test_progress_entry_is_inserted_as_newest(tmp_path):
    shutil.copy(ROOT / "progress.md", tmp_path / "progress.md")
    stats = [pipeline.RoleStats("test-writer", "claude-opus-5-5", 0.42, 9, 61.0)]
    entry = pipeline.progress_entry(FEATURES["features"][1], stats, "30 passed", date(2026, 11, 11))

    pipeline.add_progress_entry(tmp_path, entry)

    text = (tmp_path / "progress.md").read_text()
    after_separator = text.split("\n---\n", 1)[1]
    assert after_separator.startswith("\n## 2026-11-11 — F-003 Max loss rule (R2)\n")
    assert "test-writer (claude-opus-5-5, 9 turns, $0.42, 61s)" in text
    assert text.count("## 2026-11-10 — F-001") == 1  # older entries kept


def test_summarize_pytest_finds_the_result_line():
    output = "....F\nFAILED tests/x.py::t\n===== 1 failed, 4 passed in 0.03s =====\n"
    assert pipeline.summarize_pytest(output) == "1 failed, 4 passed in 0.03s"


def test_assert_only_under_reports_stray_files():
    pipeline.assert_only_under(["tests/a.py"], ("tests/",), "test-writer")
    with pytest.raises(pipeline.PipelineError, match="src/x.py"):
        pipeline.assert_only_under(["tests/a.py", "src/x.py"], ("tests/",), "test-writer")


# --- pipeline: PR description -----------------------------------------------


def test_pr_body_lists_criteria_agents_and_costs():
    roles = [
        {
            "role": "test-writer",
            "model": "claude-opus-5-5",
            "turns": 9,
            "cost_usd": 0.42,
            "seconds": 61.2,
            "attempts": 1,
            "denied_tool_calls": [],
            "summary": "Wrote 8 tests.",
        },
        {
            "role": "implementer",
            "model": "claude-opus-5-5",
            "turns": 14,
            "cost_usd": 1.1,
            "seconds": 130,
            "attempts": 2,
            "denied_tool_calls": [{"tool": "Edit"}],
            "summary": "Done.",
        },
    ]
    body = pipeline.pr_body(FEATURES["features"][1], roles, "40 passed", ["abc1234", "def5678"])

    assert "**F-003: Max loss rule (R2)**" in body
    assert "- b" in body  # acceptance criterion
    assert "| test-writer | `claude-opus-5-5` | 9 | $0.42 | 61s | 0 | 0 |" in body
    assert "| implementer | `claude-opus-5-5` | 14 | $1.10 | 130s | 1 | 1 |" in body
    assert "Commits: abc1234, def5678." in body
    assert "<summary>implementer</summary>" in body
