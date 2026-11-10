"""Tests for the PR review harness scripts in scripts/."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look the module up here
    spec.loader.exec_module(module)
    return module


boundaries = _load("check_boundaries")
submit = _load("submit_review")

FEATURES = {
    "_note": "only flip passes",
    "features": [
        {"id": "F-001", "title": "a", "acceptance": ["x"], "passes": True},
        {"id": "F-002", "title": "b", "acceptance": ["y"], "passes": False},
        {"id": "F-003", "title": "c", "acceptance": ["z"], "passes": False},
    ],
}


def feature_report(mutate):
    after = copy.deepcopy(FEATURES)
    mutate(after)
    report = boundaries.Report()
    boundaries.check_feature_list(FEATURES, after, report)
    return report


# --- check_boundaries: feature_list.json ---


def test_flipping_one_feature_is_allowed():
    report = feature_report(lambda d: d["features"][1].update(passes=True))
    assert report.violations == []
    assert report.flipped == ["F-002"]


def test_flipping_two_features_is_a_violation():
    def flip_two(d):
        d["features"][1]["passes"] = True
        d["features"][2]["passes"] = True

    report = feature_report(flip_two)
    assert any("one feature per PR" in v for v in report.violations)


def test_editing_acceptance_criteria_is_a_violation():
    report = feature_report(lambda d: d["features"][1]["acceptance"].append("easier"))
    assert any("F-002 changed ['acceptance']" in v for v in report.violations)


def test_unmarking_a_passing_feature_is_a_violation():
    report = feature_report(lambda d: d["features"][0].update(passes=False))
    assert any("passing to failing" in v for v in report.violations)


def test_removing_a_feature_is_a_violation():
    report = feature_report(lambda d: d["features"].pop())
    assert any("removed ['F-003']" in v for v in report.violations)


def test_changing_top_level_note_is_a_violation():
    report = feature_report(lambda d: d.update(_note="anything goes"))
    assert any("top-level" in v for v in report.violations)


# --- check_boundaries: protected paths ---


@pytest.mark.parametrize(
    "path",
    [
        "evals/tasks/F-002-max-daily-loss/hidden_tests/test_x.py",
        "docs/spec.md",
        ".github/workflows/pr-review.yml",
        "AGENTS.md",
        "scripts/check_boundaries.py",
    ],
)
def test_harness_paths_need_a_human(path):
    report = boundaries.Report()
    boundaries.check_paths([path, "src/challenge_engine/rules.py"], report)
    assert report.needs_human
    assert report.protected_changes == [path]


def test_engine_and_test_changes_do_not_need_a_human():
    report = boundaries.Report()
    boundaries.check_paths(
        ["src/challenge_engine/rules.py", "tests/test_x.py", "progress.md"], report
    )
    assert not report.needs_human


def test_build_report_against_a_real_git_history(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "feature_list.json").write_text(json.dumps(FEATURES))
    git("add", ".")
    git("commit", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()

    after = copy.deepcopy(FEATURES)
    after["features"][1]["passes"] = True
    (tmp_path / "feature_list.json").write_text(json.dumps(after))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("changed")
    git("add", ".")
    git("commit", "-m", "feature")

    monkeypatch.chdir(tmp_path)
    report = boundaries.build_report(base)
    assert report.violations == []
    assert report.flipped == ["F-002"]
    assert report.protected_changes == ["docs/spec.md"]


# --- submit_review: what actually gets submitted ---

APPROVE = {"verdict": "approve", "summary": "Looks right.", "blocking_issues": []}


def test_clean_approval_is_submitted_as_approval():
    flag, body = submit.decide(APPROVE, needs_human=False)
    assert flag == "--approve"
    assert "Looks right." in body


def test_harness_changes_downgrade_approval_to_comment():
    flag, body = submit.decide(APPROVE, needs_human=True)
    assert flag == "--comment"
    assert "A human must approve" in body


def test_request_changes_is_passed_through_with_issues_listed():
    review = {"verdict": "request_changes", "summary": "No.", "blocking_issues": ["uses float"]}
    flag, body = submit.decide(review, needs_human=False)
    assert flag == "--request-changes"
    assert "- uses float" in body


def test_approval_with_blocking_issues_becomes_request_changes():
    review = {**APPROVE, "blocking_issues": ["missing test"]}
    assert submit.decide(review, needs_human=False)[0] == "--request-changes"


@pytest.mark.parametrize(
    "review", [None, {}, {"verdict": "lgtm", "summary": "ok"}, {"verdict": "approve"}]
)
def test_invalid_verdict_never_approves(review):
    flag, body = submit.decide(review, needs_human=False)
    assert flag == "--comment"
    assert "failed" in body
