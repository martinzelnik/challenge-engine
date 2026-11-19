from datetime import datetime
from decimal import Decimal

from conftest import snap

from challenge_engine import (
    AccountHistory,
    ChallengeConfig,
    RuleStatus,
    Trade,
    check_min_trading_days,
)

# With the default config a challenge needs 4 distinct trading days, split at
# midnight Europe/Prague (UTC+1 in November).


def trade(opened: str, closed: str | None = None, pnl: str = "100") -> Trade:
    """Trade from ISO timestamps with offset; closed=None means still open."""
    return Trade(
        symbol="EURUSD",
        opened_at=datetime.fromisoformat(opened),
        closed_at=datetime.fromisoformat(closed) if closed is not None else None,
        pnl=Decimal(pnl),
    )


def run(config, *trades, snapshots=()):
    return check_min_trading_days(AccountHistory(snapshots=snapshots, trades=trades), config)


# --- Basic status --------------------------------------------------------------


def test_no_trades_is_pending(config):
    # Zero trading days is below the required 4.
    result = run(config)
    assert result.status is RuleStatus.PENDING
    assert result.rule == "min_trading_days"
    assert result.breached_at is None


def test_four_distinct_days_passes(config):
    # One trade on each of four local days: count 4 >= 4.
    result = run(
        config,
        trade("2026-11-02T10:00+01:00"),
        trade("2026-11-03T10:00+01:00"),
        trade("2026-11-04T10:00+01:00"),
        trade("2026-11-05T10:00+01:00"),
    )
    assert result.status is RuleStatus.PASSED
    assert result.rule == "min_trading_days"
    assert result.breached_at is None


def test_three_distinct_days_is_pending_not_failed(config):
    # 3 < 4, and the rule can never fail, only stay pending.
    result = run(
        config,
        trade("2026-11-02T10:00+01:00"),
        trade("2026-11-03T10:00+01:00"),
        trade("2026-11-04T10:00+01:00"),
    )
    assert result.status is RuleStatus.PENDING
    assert "3" in result.detail
    assert "4" in result.detail


def test_more_days_than_required_passes(config):
    # 5 >= 4.
    result = run(config, *(trade(f"2026-11-0{d}T10:00+01:00") for d in range(2, 7)))
    assert result.status is RuleStatus.PASSED


def test_uses_configured_minimum():
    # With min_trading_days=2, exactly 2 days is enough (count >= minimum) and 1 is not.
    config = ChallengeConfig(initial_balance=Decimal("100000"), min_trading_days=2)
    assert run(config, trade("2026-11-02T10:00+01:00")).status is RuleStatus.PENDING
    assert (
        run(config, trade("2026-11-02T10:00+01:00"), trade("2026-11-03T10:00+01:00")).status
        is RuleStatus.PASSED
    )


# --- AC: Several trades on one day count once ------------------------------------


def test_several_trades_on_one_day_count_once(config):
    # Five trades all opened on 2 Nov are a single trading day: 1 < 4.
    result = run(
        config,
        *(trade(f"2026-11-02T{h:02d}:00+01:00") for h in range(9, 14)),
    )
    assert result.status is RuleStatus.PENDING


def test_many_trades_over_three_days_is_still_three_days(config):
    # 2 + 3 + 2 trades over three local days count as 3 days, not 7 trades: 3 < 4.
    result = run(
        config,
        trade("2026-11-02T09:00+01:00"),
        trade("2026-11-02T15:00+01:00"),
        trade("2026-11-03T09:00+01:00"),
        trade("2026-11-03T12:00+01:00"),
        trade("2026-11-03T18:00+01:00"),
        trade("2026-11-04T09:00+01:00"),
        trade("2026-11-04T23:59+01:00"),
    )
    assert result.status is RuleStatus.PENDING


# --- AC: Days are counted by trade open time in config.timezone -----------------


def test_days_counted_by_open_time_not_close_time(config):
    # All four trades open on 2 Nov but close on four different days.
    # Only the open day counts, so that's 1 day: PENDING.
    result = run(
        config,
        trade("2026-11-02T09:00+01:00", "2026-11-02T10:00+01:00"),
        trade("2026-11-02T10:00+01:00", "2026-11-03T10:00+01:00"),
        trade("2026-11-02T11:00+01:00", "2026-11-04T10:00+01:00"),
        trade("2026-11-02T12:00+01:00", "2026-11-05T10:00+01:00"),
    )
    assert result.status is RuleStatus.PENDING


def test_trade_still_open_counts_on_its_open_day(config):
    # A trade that hasn't closed yet was still opened on a day, so it counts: 4 days.
    result = run(
        config,
        trade("2026-11-02T10:00+01:00", "2026-11-02T11:00+01:00"),
        trade("2026-11-03T10:00+01:00", "2026-11-03T11:00+01:00"),
        trade("2026-11-04T10:00+01:00", "2026-11-04T11:00+01:00"),
        trade("2026-11-05T10:00+01:00", None),
    )
    assert result.status is RuleStatus.PASSED


def test_trades_given_out_of_order_are_counted(config):
    # Trades aren't required to be sorted; four distinct open days still pass.
    result = run(
        config,
        trade("2026-11-05T10:00+01:00"),
        trade("2026-11-02T10:00+01:00"),
        trade("2026-11-04T10:00+01:00"),
        trade("2026-11-03T10:00+01:00"),
    )
    assert result.status is RuleStatus.PASSED


def test_snapshots_alone_do_not_count_as_trading_days(config):
    # Only opened trades count; equity snapshots on four days with no trades give 0 days.
    result = run(
        config,
        snapshots=(
            snap("2026-11-02T10:00+01:00", "100000"),
            snap("2026-11-03T10:00+01:00", "100000"),
            snap("2026-11-04T10:00+01:00", "100000"),
            snap("2026-11-05T10:00+01:00", "100000"),
        ),
    )
    assert result.status is RuleStatus.PENDING


def test_trade_at_local_midnight_belongs_to_the_new_day(config):
    # 23:59 on 2 Nov and 00:00 on 3 Nov are two different local days; with 4 and 5 Nov
    # that makes 4 days.
    result = run(
        config,
        trade("2026-11-02T23:59+01:00"),
        trade("2026-11-03T00:00+01:00"),
        trade("2026-11-04T10:00+01:00"),
        trade("2026-11-05T10:00+01:00"),
    )
    assert result.status is RuleStatus.PASSED


def test_uses_configured_timezone_not_prague():
    # In New York (UTC-5 in November), 03:00 UTC on 3 Nov is 22:00 on 2 Nov local,
    # and 14:00 UTC on 2 Nov is 09:00 on 2 Nov local: same local day.
    # Local days are 2, 4, 5 Nov = 3 < 4. (In Prague or UTC they would be 4 days.)
    config = ChallengeConfig(initial_balance=Decimal("100000"), timezone="America/New_York")
    result = run(
        config,
        trade("2026-11-02T14:00+00:00"),
        trade("2026-11-03T03:00+00:00"),
        trade("2026-11-04T15:00+00:00"),
        trade("2026-11-05T15:00+00:00"),
    )
    assert result.status is RuleStatus.PENDING


# --- AC: A trade opened late in the evening UTC can fall on the next local day ---


def test_late_evening_utc_trade_falls_on_next_local_day(config):
    # 23:30 UTC on 2 Nov is 00:30 on 3 Nov in Prague. Local days: 2, 3, 4, 5 Nov = 4.
    # (The 2 Nov 10:00 UTC and 23:30 UTC trades share a UTC date but not a local one.)
    result = run(
        config,
        trade("2026-11-02T10:00+00:00"),
        trade("2026-11-02T23:30+00:00"),
        trade("2026-11-04T10:00+00:00"),
        trade("2026-11-05T10:00+00:00"),
    )
    assert result.status is RuleStatus.PASSED


def test_late_evening_utc_trade_merges_with_next_local_day(config):
    # 23:30 UTC on 2 Nov (00:30 3 Nov Prague) and 10:00 UTC on 3 Nov are the same local
    # day. UTC dates would give 4 days, but local days are 3, 4, 5 Nov = 3 < 4.
    result = run(
        config,
        trade("2026-11-02T23:30+00:00"),
        trade("2026-11-03T10:00+00:00"),
        trade("2026-11-04T10:00+00:00"),
        trade("2026-11-05T10:00+00:00"),
    )
    assert result.status is RuleStatus.PENDING
