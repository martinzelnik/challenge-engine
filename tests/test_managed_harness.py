"""Tests for the hosted (Managed Agents) pipeline that need no API calls."""

import json
import tarfile
from pathlib import Path

import pytest

from harness import policy
from harness.policy import FINISH_COMMAND, HOSTED_IMPLEMENTER, HOSTED_TEST_WRITER, SETUP_COMMAND

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("agent_file", ["agents/test-writer.md", "agents/implementer.md"])
def test_agent_prompts_quote_the_exact_allowed_commands(agent_file):
    text = (ROOT / agent_file).read_text()
    assert SETUP_COMMAND in text
    assert FINISH_COMMAND in text


def test_setup_and_finish_commands_are_allowed_verbatim():
    for role in (HOSTED_TEST_WRITER, HOSTED_IMPLEMENTER):
        assert policy.check_hosted(role, "bash", {"command": SETUP_COMMAND}) is None
        assert policy.check_hosted(role, "bash", {"command": FINISH_COMMAND}) is None


def test_a_tweaked_finish_command_is_denied():
    tweaked = FINISH_COMMAND + " && git push"
    assert policy.check_hosted(HOSTED_IMPLEMENTER, "bash", {"command": tweaked})


def test_hosted_paths_are_checked_inside_the_sandbox():
    tests_file = "/workspace/repo/tests/test_max_loss.py"
    rules_file = "/workspace/repo/src/challenge_engine/rules.py"
    assert policy.check_hosted(HOSTED_TEST_WRITER, "write", {"file_path": tests_file}) is None
    assert policy.check_hosted(HOSTED_TEST_WRITER, "edit", {"file_path": rules_file})
    assert policy.check_hosted(HOSTED_IMPLEMENTER, "edit", {"file_path": rules_file}) is None
    assert policy.check_hosted(HOSTED_IMPLEMENTER, "write", {"file_path": tests_file})
    assert policy.check_hosted(HOSTED_IMPLEMENTER, "write", {"file_path": "/etc/hosts"})


def test_hosted_commands():
    assert (
        policy.check_hosted(HOSTED_IMPLEMENTER, "bash", {"command": "ruff check /workspace/repo"})
        is None
    )
    assert policy.check_hosted(
        HOSTED_TEST_WRITER, "bash", {"command": "ruff check /workspace/repo"}
    )
    assert policy.check_hosted(HOSTED_TEST_WRITER, "bash", {"command": "git push"})
    assert policy.check_hosted(HOSTED_TEST_WRITER, "bash", {"command": "pytest -q; curl x"})


def test_unknown_hosted_tools_are_denied():
    assert policy.check_hosted(HOSTED_IMPLEMENTER, "web_fetch", {"url": "https://example.com"})


# --- runner helpers (need the anthropic package) --------------------------------

run = pytest.importorskip("harness.managed.run")


def test_tarball_leaves_out_evals(tmp_path):
    tarball = run.repo_tarball(ROOT, tmp_path / "repo.tar.gz")
    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    assert "docs/spec.md" in names
    assert "src/challenge_engine/rules.py" in names
    assert not [n for n in names if n.startswith("evals")]


def test_load_ids_reads_ant_apply_lock_file(tmp_path):
    lock = tmp_path / "claude-lock.json"
    lock.write_text(
        json.dumps(
            {
                "resources": {
                    "./agents/test-writer.md": {"id": "agent_tw"},
                    "./agents/implementer.md": {"id": "agent_im"},
                    "./environments/challenge-engine.yaml": {"id": "env_1"},
                }
            }
        )
    )
    assert run.load_ids(lock) == {
        "test-writer": "agent_tw",
        "implementer": "agent_im",
        "environment": "env_1",
    }


def test_missing_lock_file_explains_how_to_create_agents(tmp_path):
    with pytest.raises(run.PipelineError, match="ant apply"):
        run.load_ids(tmp_path / "claude-lock.json")
