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


def test_tarball_mount_is_where_the_setup_command_reads_it():
    # A file's mount_path is rooted under /mnt/session/uploads/.
    assert f"{run.UPLOADS_DIR}{run.TARBALL_MOUNT}" in SETUP_COMMAND


@pytest.mark.parametrize(("usd", "cents"), [(2, "200"), (0.5, "50"), (1.25, "125"), (3.0, "300")])
def test_budget_is_sent_in_cents(usd, cents):
    assert run.budget_cents(usd) == cents


def test_zero_budget_is_rejected():
    with pytest.raises(run.PipelineError):
        run.budget_cents(0)


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo_with_hidden_test(tmp_path):
    repo = tmp_path / "repo"
    (repo / "evals/tasks/x/hidden_tests").mkdir(parents=True)
    (repo / "evals/tasks/x/hidden_tests/test_secret.py").write_text("SECRET = 1\n")
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("A = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_a.py").write_text("")
    (repo / ".gitignore").write_text("ignored.txt\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return repo


def test_checks_copy_has_the_working_tree_but_not_evals(tmp_path):
    repo, copy = _repo_with_hidden_test(tmp_path), tmp_path / "copy"
    (repo / "src/a.py").write_text("A = 2\n")  # the agent's uncommitted patch
    (repo / "src/b.py").write_text("B = 1\n")  # a new file from the patch
    (repo / "ignored.txt").write_text("x\n")

    run.isolated_copy(repo, copy)

    assert (copy / "src/a.py").read_text() == "A = 2\n"
    assert (copy / "src/b.py").exists()
    assert not (copy / "evals").exists()
    assert not (copy / "ignored.txt").exists()
    assert (copy / ".git").is_dir()  # tests that call git still work in the copy


def test_checks_copy_skips_symlinks_into_evals(tmp_path):
    repo, copy = _repo_with_hidden_test(tmp_path), tmp_path / "copy"
    (repo / "tests/test_x.py").symlink_to("../evals/tasks/x/hidden_tests/test_secret.py")
    (repo / "tests/hidden").symlink_to("../evals/tasks/x/hidden_tests")

    run.isolated_copy(repo, copy)

    assert not (copy / "tests/test_x.py").exists()
    assert not (copy / "tests/hidden").exists()
    assert "SECRET" not in "".join(
        f.read_text() for f in copy.rglob("*.py") if ".git" not in f.parts
    )


def test_a_patch_that_adds_a_symlink_is_refused(tmp_path):
    patch = (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "new file mode 120000\n"
        "--- /dev/null\n+++ b/tests/test_x.py\n@@ -0,0 +1 @@\n"
        "+../evals/tasks/x/hidden_tests/test_secret.py\n\\ No newline at end of file\n"
    )
    with pytest.raises(run.PipelineError, match="symlink"):
        run.reject_symlinks(patch, tmp_path, [])


def test_a_symlink_among_changed_paths_is_refused(tmp_path):
    repo = _repo_with_hidden_test(tmp_path)
    (repo / "tests/test_x.py").symlink_to("../evals/tasks/x/hidden_tests/test_secret.py")
    with pytest.raises(run.PipelineError, match="tests/test_x.py"):
        run.reject_symlinks("", repo, ["tests/test_x.py"])
    run.reject_symlinks("", repo, ["src/a.py"])  # plain files are fine


def test_counts_come_from_the_junit_report_not_stdout(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest" tests="6" failures="2" errors="1" '
        'skipped="1"></testsuite></testsuites>'
    )
    counts = run.junit_counts(report)
    assert counts == {"tests": 6, "failures": 2, "errors": 1, "skipped": 1, "passed": 2}
    assert run.counts_summary(counts) == "2 failed, 2 passed, 1 errors, 1 skipped"
    assert run.junit_counts(tmp_path / "missing.xml") == {}
    assert run.counts_summary({}) == "no results"


def test_review_round_entry_follows_the_progress_template():
    from datetime import date

    entry = run.review_round_entry({"id": "F-003"}, 1, "30 passed", date(2026, 11, 11))
    assert entry.startswith("## 2026-11-11 — F-003 review round 1\n")
    for field in ("- Agent/model:", "- Done:", "- Tests: 30 passed", "- Next:"):
        assert field in entry
