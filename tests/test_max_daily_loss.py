from datetime import datetime
from decimal import Decimal

from conftest import snap

from challenge_engine import AccountHistory, ChallengeConfig, RuleStatus, check_max_daily_loss

# With the default config (100,000 initial, 5%) a day may lose at most 5,000.


def run(config, *snapshots):
    return check_max_daily_loss(AccountHistory(snapshots=snapshots), config)


def test_no_activity_passes(config):
    result = run(config)
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None


def test_loss_within_limit_passes(config):
    result = run(config, snap("2026-11-02T10:00+01:00", "100000", "96500"))
    assert result.status is RuleStatus.PASSED
    assert result.rule == "max_daily_loss"


def test_breach_reports_first_breach_time(config):
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "95500"),
        snap("2026-11-02T10:05+01:00", "100000", "94900"),
        snap("2026-11-02T10:10+01:00", "100000", "93000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:05+01:00")
    assert "95,000.00" in result.detail
    assert "94,900.00" in result.detail


def test_equity_exactly_at_limit_is_allowed(config):
    assert run(config, snap("2026-11-02T10:00+01:00", "100000", "95000")).status is (
        RuleStatus.PASSED
    )


def test_one_cent_below_limit_fails(config):
    assert run(config, snap("2026-11-02T10:00+01:00", "100000", "94999.99")).status is (
        RuleStatus.FAILED
    )


def test_day_start_balance_carries_over_from_previous_day(config):
    # Day 1 ends at 103,000 balance, so day 2's limit is 98,000.
    history = [snap("2026-11-02T17:00+01:00", "103000")]
    assert run(config, *history, snap("2026-11-03T09:00+01:00", "103000", "98000")).status is (
        RuleStatus.PASSED
    )
    failed = run(config, *history, snap("2026-11-03T09:00+01:00", "103000", "97999"))
    assert failed.status is RuleStatus.FAILED
    assert "98,000.00" in failed.detail


def test_day_start_balance_ignores_floating_pnl_at_close(config):
    # Day 1 closes with balance 100,000 but equity 108,000 (open profit).
    # The day-start balance for day 2 is the balance, so the limit is 95,000.
    result = run(
        config,
        snap("2026-11-02T17:00+01:00", "100000", "108000"),
        snap("2026-11-03T09:00+01:00", "100000", "95500"),
    )
    assert result.status is RuleStatus.PASSED


def test_closed_losses_during_the_day_count_towards_the_limit(config):
    # A 3,000 loss is closed mid-day; the limit stays anchored to the day start (95,000),
    # not to the new balance (which would give 92,000).
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "97000", "97000"),
        snap("2026-11-02T11:00+01:00", "97000", "94500"),
    )
    assert result.status is RuleStatus.FAILED


def test_each_day_gets_its_own_limit(config):
    # -4,000 per day for three days: never more than 5,000 in a single day.
    result = run(
        config,
        snap("2026-11-02T17:00+01:00", "96000"),
        snap("2026-11-03T17:00+01:00", "92000"),
        snap("2026-11-04T17:00+01:00", "88000"),
    )
    assert result.status is RuleStatus.PASSED


def test_days_split_at_local_midnight_not_utc(config):
    # 23:30 UTC on 2 Nov is 00:30 on 3 Nov in Prague (UTC+1), so the second snapshot
    # starts a new day with a 96,000 start balance and a 91,000 limit.
    result = run(
        config,
        snap("2026-11-02T22:00+00:00", "96000"),
        snap("2026-11-02T23:30+00:00", "96000", "92000"),
    )
    assert result.status is RuleStatus.PASSED


def test_same_utc_day_can_be_two_local_days_in_another_timezone():
    # In New York, 03:00 UTC on 3 Nov is still 2 Nov (22:00 local).
    config = ChallengeConfig(initial_balance=Decimal("100000"), timezone="America/New_York")
    result = run(
        config,
        snap("2026-11-03T03:00+00:00", "96000"),  # 2 Nov local
        snap("2026-11-03T06:00+00:00", "96000", "92000"),  # 3 Nov local, limit 91,000
    )
    assert result.status is RuleStatus.PASSED


def test_snapshot_at_local_midnight_belongs_to_the_new_day(config):
    # Exactly midnight starts the new day, so its start balance is the 97,000 from before.
    result = run(
        config,
        snap("2026-11-02T23:59+01:00", "97000"),
        snap("2026-11-03T00:00+01:00", "97000", "92500"),
    )
    assert result.status is RuleStatus.PASSED


def test_uses_configured_percentage():
    config = ChallengeConfig(initial_balance=Decimal("50000"), max_daily_loss_pct=Decimal("0.03"))
    # Limit is 50,000 - 1,500 = 48,500.
    assert run(config, snap("2026-11-02T10:00+01:00", "50000", "48500")).status is (
        RuleStatus.PASSED
    )
    assert run(config, snap("2026-11-02T10:00+01:00", "50000", "48499")).status is (
        RuleStatus.FAILED
    )
