"""Hidden tests for F-002. Agents must not read these. Self-contained on purpose."""

from datetime import datetime
from decimal import Decimal

from challenge_engine import (
    AccountHistory,
    ChallengeConfig,
    EquitySnapshot,
    RuleStatus,
    check_max_daily_loss,
)

CONFIG = ChallengeConfig(initial_balance=Decimal("100000"))  # daily limit = 5,000


def snap(iso, balance, equity=None):
    return EquitySnapshot(
        at=datetime.fromisoformat(iso),
        balance=Decimal(balance),
        equity=Decimal(equity if equity is not None else balance),
    )


def run(*snaps, config=CONFIG):
    return check_max_daily_loss(AccountHistory(snapshots=snaps), config)


def test_empty_history_passes():
    assert run().status is RuleStatus.PASSED


def test_small_loss_passes():
    result = run(snap("2026-11-02T10:00+01:00", "100000", "97000"))
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None


def test_equity_below_limit_fails_with_first_breach_time():
    result = run(
        snap("2026-11-02T10:00+01:00", "100000", "96000"),
        snap("2026-11-02T11:00+01:00", "100000", "94999"),
        snap("2026-11-02T12:00+01:00", "100000", "94000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T11:00+01:00")


def test_equity_exactly_at_limit_is_allowed():
    assert run(snap("2026-11-02T10:00+01:00", "100000", "95000")).status is RuleStatus.PASSED


def test_limit_moves_with_previous_day_closing_balance():
    # Day 1 closes at 104,000, so day 2's limit is 104,000 - 5,000 = 99,000.
    ok = run(
        snap("2026-11-02T16:00+01:00", "104000"),
        snap("2026-11-03T10:00+01:00", "104000", "99000"),
    )
    assert ok.status is RuleStatus.PASSED

    bad = run(
        snap("2026-11-02T16:00+01:00", "104000"),
        snap("2026-11-03T10:00+01:00", "104000", "98999"),
    )
    assert bad.status is RuleStatus.FAILED


def test_loss_spread_over_two_days_does_not_breach():
    # -4,000 on day 1, another -4,000 on day 2: each day is within its own limit.
    result = run(
        snap("2026-11-02T16:00+01:00", "96000"),
        snap("2026-11-03T16:00+01:00", "92000"),
    )
    assert result.status is RuleStatus.PASSED


def test_day_boundary_uses_config_timezone_not_utc():
    # 23:30 UTC on Nov 2 is 00:30 on Nov 3 in Prague, so the 96,000 closing
    # balance at 22:00 UTC (23:00 Prague) is day 2's start balance.
    # Day 2 limit = 96,000 - 5,000 = 91,000; equity 92,000 is fine.
    # A UTC-based engine would use 100,000 as the start and flag a breach.
    result = run(
        snap("2026-11-02T22:00+00:00", "96000"),
        snap("2026-11-02T23:30+00:00", "96000", "92000"),
    )
    assert result.status is RuleStatus.PASSED


def test_detail_mentions_the_limit():
    result = run(snap("2026-11-02T10:00+01:00", "100000", "94000"))
    assert result.status is RuleStatus.FAILED
    assert "95,000" in result.detail or "95000" in result.detail
