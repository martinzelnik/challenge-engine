"""F-006: `python -m challenge_engine account.json`.

The spec does not define the JSON layout, so these tests fix the obvious one that
mirrors the models (money as strings, ISO datetimes with offsets):

    {
      "config": {"initial_balance": "100000", ...optional ChallengeConfig fields},
      "snapshots": [{"at": "...", "balance": "...", "equity": "..."}],
      "trades": [{"symbol": "...", "opened_at": "...", "closed_at": "..." | null, "pnl": "..."}]
    }

Default config: 100,000 initial balance, daily allowance 5,000 (R1), overall floor
90,000 (R2), profit target 110,000 balance (R3), 4 trading days (R4).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from challenge_engine import AccountHistory, ChallengeConfig, EquitySnapshot, Trade, evaluate


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "challenge_engine", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_account(tmp_path: Path, data: object, name: str = "account.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


def snapshot(at: str, balance: str, equity: str | None = None) -> dict:
    return {"at": at, "balance": balance, "equity": equity if equity is not None else balance}


def trade(opened_at: str, pnl: str = "100") -> dict:
    return {"symbol": "EURUSD", "opened_at": opened_at, "closed_at": opened_at, "pnl": pnl}


def account(snapshots=(), trades=(), **config) -> dict:
    return {
        "config": {"initial_balance": "100000", **config},
        "snapshots": list(snapshots),
        "trades": list(trades),
    }


def expected_verdict(data: dict):
    """The verdict the engine itself gives for the same account, built from the models."""
    cfg = ChallengeConfig(
        **{
            k: (v if k in ("timezone", "min_trading_days") else Decimal(v))
            for k, v in data["config"].items()
        }
    )
    history = AccountHistory(
        snapshots=tuple(
            EquitySnapshot(
                datetime.fromisoformat(s["at"]), Decimal(s["balance"]), Decimal(s["equity"])
            )
            for s in data["snapshots"]
        ),
        trades=tuple(
            Trade(
                t["symbol"],
                datetime.fromisoformat(t["opened_at"]),
                datetime.fromisoformat(t["closed_at"]) if t["closed_at"] else None,
                Decimal(t["pnl"]),
            )
            for t in data["trades"]
        ),
    )
    return evaluate(history, cfg)


TRADES_4_DAYS = [trade(f"2026-11-{d:02d}T10:00+01:00") for d in (2, 3, 4, 5)]

PASSED_ACCOUNT = account(
    [
        snapshot("2026-11-02T17:00+01:00", "102000"),
        snapshot("2026-11-03T17:00+01:00", "105000"),
        snapshot("2026-11-04T17:00+01:00", "108000"),
        snapshot("2026-11-05T17:00+01:00", "110000"),
    ],
    TRADES_4_DAYS,
)

FAILED_ACCOUNT = account([snapshot("2026-11-02T10:00+01:00", "100000", "94000")])

IN_PROGRESS_ACCOUNT = account(
    [snapshot("2026-11-03T17:00+01:00", "104000")],
    [trade("2026-11-02T10:00+01:00"), trade("2026-11-03T10:00+01:00")],
)


def assert_clean_error(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
    assert result.stderr.strip(), "expected an error message on stderr"


# --- Prints the verdict and each rule's detail ---


def test_prints_passed_verdict_and_every_rule_detail(tmp_path):
    # Balance reaches 110,000 over 4 trading days with no drawdown: PASSED, and the
    # output repeats each rule's detail sentence so a trader can see why.
    result = run_cli(str(write_account(tmp_path, PASSED_ACCOUNT)))
    verdict = expected_verdict(PASSED_ACCOUNT)
    assert "PASSED" in result.stdout
    for rule in verdict.rules:
        assert rule.detail in result.stdout


def test_prints_failed_verdict_and_every_rule_detail(tmp_path):
    # Equity 94,000 is below the 95,000 daily limit: FAILED. All four details still print,
    # including R1's breach sentence with the numbers.
    result = run_cli(str(write_account(tmp_path, FAILED_ACCOUNT)))
    verdict = expected_verdict(FAILED_ACCOUNT)
    assert "FAILED" in result.stdout
    assert "94,000.00" in result.stdout
    assert len(verdict.rules) == 4
    for rule in verdict.rules:
        assert rule.detail in result.stdout


def test_prints_in_progress_verdict_and_every_rule_detail(tmp_path):
    # Balance 104,000 is short of 110,000 and only 2 trading days: IN_PROGRESS.
    result = run_cli(str(write_account(tmp_path, IN_PROGRESS_ACCOUNT)))
    verdict = expected_verdict(IN_PROGRESS_ACCOUNT)
    assert "IN_PROGRESS" in result.stdout
    for rule in verdict.rules:
        assert rule.detail in result.stdout


def test_config_fields_from_the_file_are_used(tmp_path):
    # With min_trading_days lowered to 2 and profit target 4%, the IN_PROGRESS account
    # above (balance 104,000, 2 days) now passes: the CLI must honour the file's config.
    data = account(
        IN_PROGRESS_ACCOUNT["snapshots"],
        IN_PROGRESS_ACCOUNT["trades"],
        profit_target_pct="0.04",
        min_trading_days=2,
    )
    result = run_cli(str(write_account(tmp_path, data)))
    assert result.returncode == 0
    assert "PASSED" in result.stdout
    assert "IN_PROGRESS" not in result.stdout


def test_open_trade_with_null_closed_at_is_accepted(tmp_path):
    # closed_at is null for open trades (spec Inputs); that is valid input, not an error.
    data = account(
        [snapshot("2026-11-02T17:00+01:00", "100000", "100500")],
        [
            {
                "symbol": "EURUSD",
                "opened_at": "2026-11-02T10:00+01:00",
                "closed_at": None,
                "pnl": "500",
            }
        ],
    )
    result = run_cli(str(write_account(tmp_path, data)))
    assert result.returncode == 0
    assert "IN_PROGRESS" in result.stdout


# --- Exit code 0 for PASSED or IN_PROGRESS, 1 for FAILED, 2 for invalid input ---


def test_exit_code_0_for_passed(tmp_path):
    result = run_cli(str(write_account(tmp_path, PASSED_ACCOUNT)))
    assert result.returncode == 0


def test_exit_code_0_for_in_progress(tmp_path):
    result = run_cli(str(write_account(tmp_path, IN_PROGRESS_ACCOUNT)))
    assert result.returncode == 0


def test_exit_code_1_for_failed(tmp_path):
    # Equity 94,000 < 95,000 daily limit: a real FAILED verdict, not just any exit 1.
    result = run_cli(str(write_account(tmp_path, FAILED_ACCOUNT)))
    assert result.returncode == 1
    assert "FAILED" in result.stdout
    assert "Traceback" not in result.stderr


def test_exit_code_1_for_overall_max_loss_breach(tmp_path):
    # Equity 89,999.99 is strictly below the 90,000 floor (R2): FAILED -> exit 1.
    data = account(
        [
            snapshot("2026-11-02T17:00+01:00", "96000"),
            snapshot("2026-11-03T17:00+01:00", "92000"),
            snapshot("2026-11-04T10:00+01:00", "92000", "89999.99"),
        ]
    )
    result = run_cli(str(write_account(tmp_path, data)))
    assert result.returncode == 1
    assert "FAILED" in result.stdout


def test_equity_exactly_at_limit_exits_0(tmp_path):
    # Equity exactly 95,000 sits at R1's daily limit, which is allowed: not FAILED.
    data = account([snapshot("2026-11-02T10:00+01:00", "100000", "95000")])
    result = run_cli(str(write_account(tmp_path, data)))
    assert result.returncode == 0
    assert "FAILED" not in result.stdout


# --- Invalid input: exit 2 with a clear error, not a traceback ---


def test_missing_file_is_a_clean_error(tmp_path):
    missing = tmp_path / "nope.json"
    result = run_cli(str(missing))
    assert_clean_error(result)
    assert "nope.json" in result.stderr


def test_no_argument_is_a_clean_error():
    result = run_cli()
    assert_clean_error(result)


def test_malformed_json_is_a_clean_error(tmp_path):
    result = run_cli(str(write_account(tmp_path, '{"config": {')))
    assert_clean_error(result)


def test_missing_config_is_a_clean_error(tmp_path):
    data = {"snapshots": [], "trades": []}
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_missing_initial_balance_is_a_clean_error(tmp_path):
    data = account()
    del data["config"]["initial_balance"]
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_non_numeric_money_is_a_clean_error(tmp_path):
    data = account([snapshot("2026-11-02T10:00+01:00", "lots")])
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_invalid_datetime_is_a_clean_error(tmp_path):
    data = account([snapshot("yesterday", "100000")])
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_naive_datetime_is_a_clean_error(tmp_path):
    # The models reject naive datetimes; the CLI must report that, not crash.
    data = account([snapshot("2026-11-02T10:00", "100000")])
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_unsorted_snapshots_is_a_clean_error(tmp_path):
    data = account(
        [
            snapshot("2026-11-03T10:00+01:00", "100000"),
            snapshot("2026-11-02T10:00+01:00", "100000"),
        ]
    )
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_unknown_timezone_is_a_clean_error(tmp_path):
    data = account(timezone="Mars/Olympus_Mons")
    result = run_cli(str(write_account(tmp_path, data)))
    assert_clean_error(result)


def test_top_level_not_an_object_is_a_clean_error(tmp_path):
    result = run_cli(str(write_account(tmp_path, [1, 2, 3])))
    assert_clean_error(result)


def test_invalid_input_prints_nothing_on_stdout_as_a_verdict(tmp_path):
    # An invalid file must not be reported as a verdict.
    result = run_cli(str(write_account(tmp_path, "not json")))
    assert_clean_error(result)
    for status in ("PASSED", "FAILED", "IN_PROGRESS"):
        assert status not in result.stdout
