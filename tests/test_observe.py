"""Tests for the agent dashboard's data layer and its run controls (observe/)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from observe import data
from observe.server import Dashboard, RunManager, StartError, make_handler

FEATURES = [
    {"id": "F-001", "title": "Models and profit target rule (R3)", "passes": True},
    {"id": "F-005", "title": "Overall verdict (evaluate)", "passes": False},
    {"id": "F-006", "title": "CLI: evaluate an account from a JSON file", "passes": False},
]


def run_record(feature: str, at: str, outcome: str = "passed", **extra) -> dict:
    return {
        "at": at,
        "feature": feature,
        "outcome": outcome,
        "roles": [
            {
                "role": "test-writer",
                "cost_usd": 0.4,
                "turns": 14,
                "seconds": 57.0,
                "denied_tool_calls": [
                    {"tool": "Bash", "reason": "Run one plain command: no pipes."},
                    {"tool": "Glob", "reason": "Pass a `path` to Glob."},
                ],
            },
            {
                "role": "implementer",
                "cost_usd": 0.2,
                "turns": 12,
                "seconds": 21.5,
                "denied_tool_calls": [
                    {"tool": "Bash", "reason": "Run one plain command: no pipes."}
                ],
            },
        ],
        **extra,
    }


# --- run ledger ---------------------------------------------------------------


def test_load_runs_newest_first_with_totals_and_skips_bad_lines(tmp_path: Path):
    ledger = tmp_path / "logs" / "agent-runs.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(
        json.dumps(run_record("F-003", "2026-11-19T20:19:10+00:00"))
        + "\nnot json\n"
        + json.dumps(run_record("F-004", "2026-11-19T20:20:50+00:00"))
        + "\n"
    )
    runs = data.load_runs(tmp_path)
    assert [r["feature"] for r in runs] == ["F-004", "F-003"]
    assert runs[0]["total_cost_usd"] == pytest.approx(0.6)
    assert runs[0]["total_turns"] == 26
    assert runs[0]["total_denials"] == 3


def test_load_runs_merges_branch_ledgers_without_duplicates(tmp_path: Path):
    ledger = tmp_path / "logs" / "agent-runs.jsonl"
    ledger.parent.mkdir()
    main_line = json.dumps(run_record("F-004", "2026-11-19T20:20:50+00:00"))
    ledger.write_text(main_line + "\n")
    branch = main_line + "\n" + json.dumps(run_record("F-005", "2026-11-19T21:00:00+00:00"))
    runs = data.load_runs(tmp_path, [branch])
    assert [r["feature"] for r in runs] == ["F-005", "F-004"]


def test_hosted_run_costs_are_flagged_and_left_out_of_spend():
    local = data._with_totals(run_record("F-004", "b"))
    hosted = data._with_totals(run_record("F-003", "a", runner="managed-agents"))
    assert hosted["cost_unverified"] and not local["cost_unverified"]
    numbers = data.overview([local, hosted], [])
    assert numbers["total_cost_usd"] == pytest.approx(0.6)
    assert numbers["runs"] == 2 and numbers["runs_passed"] == 2 and numbers["runs_unpriced"] == 1


def test_load_runs_without_a_ledger_is_empty(tmp_path: Path):
    assert data.load_runs(tmp_path) == []


def test_denial_summary_groups_by_role_tool_and_reason():
    runs = [
        data._with_totals(run_record("F-003", "a")),
        data._with_totals(run_record("F-004", "b")),
    ]
    summary = data.denial_summary(runs)
    top = summary[0]
    assert top["count"] == 2 and top["runs"] == 2
    assert {(d["role"], d["tool"]) for d in summary} == {
        ("test-writer", "Bash"),
        ("test-writer", "Glob"),
        ("implementer", "Bash"),
    }


# --- run logs -----------------------------------------------------------------

LOG = """\
F-006 CLI: evaluate an account from a JSON file -> branch feat/F-006-cli
1/2 test-writer
  [test-writer] Bash cat docs/spec.md && ls
  [test-writer] denied Bash: Run one plain command: no pipes, chaining, redirects or substitutions.
  [test-writer] Read docs/spec.md
  [test-writer] Glob
Tests committed as d78063d: 22 failed in 1.00s
2/2 implementer
  [implementer] Write src/challenge_engine/cli.py
  [implementer] denied Bash: The implementer may only run: .venv/bin/pytest, .venv/bin/ruff.

Done: F-006 implemented on feat/F-006-cli (166 passed in 2.42s, $0.87).
"""


def test_parse_local_denial_keeps_the_whole_reason():
    event = data.parse_log_line(
        "  [implementer] denied Bash: The implementer may only run: .venv/bin/pytest."
    )
    assert event == {
        "kind": "denied",
        "role": "implementer",
        "tool": "Bash",
        "target": "",
        "reason": "The implementer may only run: .venv/bin/pytest.",
    }


def test_parse_hosted_denial_splits_target_and_reason():
    event = data.parse_log_line(
        "  [test-writer] DENIED bash ruff check tests/x.py: The test-writer may only run: pytest."
    )
    assert event["kind"] == "denied"
    assert event["target"] == "ruff check tests/x.py"
    assert event["reason"] == "The test-writer may only run: pytest."


def test_parse_tool_watch_and_pr_lines():
    assert data.parse_log_line("  [test-writer] Read docs/spec.md")["kind"] == "tool"
    assert data.parse_log_line("  [test-writer] Glob ")["target"] == ""
    assert data.parse_log_line("  watch:   https://console.example/s/1") == {
        "kind": "link",
        "label": "Console",
        "url": "https://console.example/s/1",
    }
    assert data.parse_log_line("PR opened: https://github.com/o/r/pull/9")["label"] == "PR"


def test_summarize_log_counts_calls_and_finds_outcome():
    summary = data.summarize_log(LOG)
    assert summary["feature"] == "F-006"
    assert summary["branch"] == "feat/F-006-cli"
    assert summary["phase"] == "implementer"
    assert summary["tool_calls"] == 4
    assert summary["denials"] == 2
    assert summary["outcome"] == "passed"


def test_summarize_log_unfinished_is_running_and_stopped_is_failed():
    assert data.summarize_log("F-005 X -> branch feat/F-005-x\n1/2 test-writer\n")["outcome"] == (
        "running"
    )
    assert data.summarize_log("Pipeline stopped: budget\n")["outcome"] == "failed"
    assert data.summarize_log("whatever\nEXIT 1\n")["outcome"] == "failed"


def test_safe_log_path_refuses_traversal(tmp_path: Path):
    (tmp_path / "F-006.log").write_text(LOG)
    (tmp_path.parent / "secret.log").write_text("no")
    assert data.safe_log_path([tmp_path], "F-006.log") == (tmp_path / "F-006.log").resolve()
    assert data.safe_log_path([tmp_path], "../secret.log") is None
    assert data.safe_log_path([tmp_path], "F-006.txt") is None
    assert data.safe_log_path([tmp_path], "missing.log") is None


def test_read_from_tails_by_offset(tmp_path: Path):
    path = tmp_path / "run.log"
    path.write_text("one\ntwo\n")
    text, offset = data.read_from(path, 0)
    assert text == "one\ntwo\n"
    with path.open("a") as f:
        f.write("three\n")
    assert data.read_from(path, offset) == ("three\n", offset + 6)
    # a file that was replaced by a shorter one is read from the start
    path.write_text("x\n")
    assert data.read_from(path, offset) == ("x\n", 2)


def test_read_from_never_splits_a_line_when_the_chunk_is_full(tmp_path: Path):
    path = tmp_path / "run.log"
    path.write_text("aaaa\nbbbb\n")
    assert data.read_from(path, 0, limit=7) == ("aaaa\n", 5)


# --- transcripts --------------------------------------------------------------


def test_find_and_parse_transcript(tmp_path: Path):
    session = "8dcca4eb-7eb6-498a-8df9-08e5d143df0a"
    folder = tmp_path / "-home-user-challenge-engine"
    folder.mkdir()
    lines = [
        {"type": "user", "message": {"role": "user", "content": "Write the tests"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading the spec."},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "docs/spec.md"}},
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "content": "denied", "is_error": True}],
            },
        },
        {"type": "summary"},
    ]
    (folder / f"{session}.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    path = data.find_transcript(session, tmp_path)
    assert path is not None
    entries = data.parse_transcript(path.read_text())
    assert [e["kind"] for e in entries] == ["text", "text", "tool_use", "tool_result"]
    assert entries[2]["tool"] == "Read" and entries[3]["error"] is True
    assert data.find_transcript("../../etc/passwd", tmp_path) is None


# --- board --------------------------------------------------------------------


def test_feature_board_stages_from_commits_and_prs():
    runs = [data._with_totals(run_record("F-005", "2026-11-19T21:00:00+00:00"))]
    b5 = data.branch_name(FEATURES[1])
    b6 = data.branch_name(FEATURES[2])
    branches = {
        b5: (b5, [{"sha": "1", "at": "", "subject": "test(F-005): failing tests"}]),
        b6: (
            f"origin/{b6}",
            [
                {"sha": "2", "at": "", "subject": "feat(F-006): CLI"},
                {"sha": "3", "at": "", "subject": "test(F-006): failing tests"},
            ],
        ),
    }
    prs = [{"number": 11, "headRefName": b6, "state": "OPEN", "reviewDecision": "APPROVED"}]
    board = {row["id"]: row for row in data.feature_board(FEATURES, runs, branches, prs)}

    assert all(board["F-001"]["stages"].values())  # passing with no branch: merged earlier
    assert board["F-005"]["stages"] == {
        "tests": True,
        "code": False,
        "pr": False,
        "review": False,
        "merged": False,
    }
    assert board["F-005"]["cost_usd"] == pytest.approx(0.6)
    assert board["F-005"]["runs"] == 1
    assert board["F-006"]["stages"]["review"] and not board["F-006"]["stages"]["merged"]
    assert board["F-006"]["pr"]["number"] == 11


def test_feature_on_main_is_merged_even_with_a_leftover_branch():
    b6 = data.branch_name(FEATURES[2])
    branches = {b6: (f"origin/{b6}", [{"sha": "1", "at": "", "subject": "test(F-006): t"}])}
    row = data.feature_board(FEATURES[2:], [], branches, None, merged={"F-006"})[0]
    assert all(row["stages"].values())
    row = data.feature_board(FEATURES[2:], [], branches, None, merged=set())[0]
    assert row["stages"]["tests"] and not row["stages"]["merged"]


def test_feature_board_marks_changes_requested_and_merged():
    b6 = data.branch_name(FEATURES[2])
    prs = [{"number": 9, "headRefName": b6, "state": "OPEN", "reviewDecision": "CHANGES_REQUESTED"}]
    row = data.feature_board(FEATURES[2:], [], {}, prs)[0]
    assert row["changes_requested"] and not row["stages"]["review"]
    prs = [{"number": 9, "headRefName": b6, "state": "MERGED", "reviewDecision": ""}]
    row = data.feature_board(FEATURES[2:], [], {}, prs)[0]
    assert all(row["stages"].values())


def test_github_repo_from_remote_urls():
    assert data.github_repo("https://github.com/martinzelnik/challenge-engine") == (
        "martinzelnik/challenge-engine"
    )
    assert data.github_repo("git@github.com:o/r.git\n") == "o/r"
    assert data.github_repo(None) is None


# --- run control --------------------------------------------------------------


def test_validate_builds_the_pipeline_command():
    manager = RunManager(Path("/repo"), "/py")
    cmd = manager.validate({"feature": "F-005", "budget": 2}, FEATURES)
    assert cmd == ["/py", "-m", "harness.run_feature", "F-005", "--budget", "2", "--no-push"]
    cmd = manager.validate({"feature": "F-006", "mode": "hosted", "push": True}, FEATURES)
    assert cmd[2] == "harness.managed.run" and "--no-push" not in cmd


@pytest.mark.parametrize(
    "request_body, message",
    [
        ({"feature": "F-001"}, "already passes"),
        ({"feature": "F-999"}, "No feature"),
        ({"feature": "F-005; rm -rf /"}, "No feature"),
        ({"feature": "F-005", "mode": "shell"}, "Unknown mode"),
        ({"feature": "F-005", "budget": 0}, "Budget"),
        ({"feature": "F-005", "budget": 500}, "Budget"),
        ({"feature": "F-005", "budget": "lots"}, "Budget"),
    ],
)
def test_validate_refuses_bad_requests(request_body: dict, message: str):
    with pytest.raises(StartError, match=message):
        RunManager(Path("/repo"), "/py").validate(request_body, FEATURES)


def test_stop_without_a_run_is_refused():
    with pytest.raises(StartError, match="Nothing is running"):
        RunManager(Path("/repo"), "/py").stop()


@pytest.fixture
def server(tmp_path: Path):
    (tmp_path / "feature_list.json").write_text(json.dumps({"features": FEATURES}))
    app = Dashboard(tmp_path, [], github=False, projects_dir=tmp_path / "projects")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _request(url: str, body: dict | None = None, headers: dict | None = None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_page_carries_the_token_and_state_is_json(server):
    app, base = server
    status, page = _request(base + "/")
    assert status == 200 and app.token in page and "__TOKEN__" not in page
    status, body = _request(base + "/api/state")
    state = json.loads(body)
    assert status == 200
    assert [f["id"] for f in state["board"]] == ["F-001", "F-005", "F-006"]
    assert state["runs"] == [] and state["active"] is None


def test_starting_a_run_needs_the_token(server):
    _, base = server
    status, body = _request(base + "/api/runs", {"feature": "F-005"})
    assert status == 403 and "token" in body


def test_requests_for_a_foreign_host_are_refused(server):
    _, base = server
    status, _ = _request(base + "/api/state", headers={"Host": "evil.example:8765"})
    assert status == 403


def test_log_endpoint_refuses_unknown_names(server):
    _, base = server
    status, _ = _request(base + "/api/log?name=..%2Ffeature_list.json")
    assert status == 404
