from datetime import datetime
from decimal import Decimal

from conftest import snap

from challenge_engine import (
    AccountHistory,
    RuleStatus,
    Trade,
    VerdictStatus,
    check_max_daily_loss,
    check_max_loss,
    check_min_trading_days,
    check_profit_target,
    evaluate,
)

# Default config: 100,000 initial balance, daily allowance 5,000 (R1), overall
# floor 90,000 (R2), profit target 110,000 balance (R3), 4 trading days (R4).
# Europe/Prague is UTC+1 in November.

RULE_ORDER = ["max_daily_loss", "max_loss", "profit_target", "min_trading_days"]


def trade(opened: str, pnl: str = "100") -> Trade:
    """A closed trade opened at the given ISO time (with offset)."""
    at = datetime.fromisoformat(opened)
    return Trade(symbol="EURUSD", opened_at=at, closed_at=at, pnl=Decimal(pnl))


def trades_on_days(*days: int) -> tuple[Trade, ...]:
    """One trade at 10:00 Prague time on each given November 2026 day."""
    return tuple(trade(f"2026-11-{day:02d}T10:00+01:00") for day in days)


def history(snapshots=(), trades=()) -> AccountHistory:
    return AccountHistory(snapshots=tuple(snapshots), trades=tuple(trades))


# --- Returns rule results in order R1-R4 ---


def test_rules_are_returned_in_order_r1_to_r4(config):
    # The spec fixes the order: max daily loss, max loss, profit target, min trading days.
    verdict = evaluate(history(), config)
    assert [r.rule for r in verdict.rules] == RULE_ORDER


def test_rule_results_match_the_individual_rule_checks(config):
    # evaluate just runs the four rules; each entry equals calling that rule directly.
    h = history(
        [
            snap("2026-11-02T10:00+01:00", "100000", "94000"),
            snap("2026-11-02T17:00+01:00", "102000"),
        ],
        trades_on_days(2, 3),
    )
    verdict = evaluate(h, config)
    assert list(verdict.rules) == [
        check_max_daily_loss(h, config),
        check_max_loss(h, config),
        check_profit_target(h, config),
        check_min_trading_days(h, config),
    ]


def test_order_is_kept_when_rules_fail(config):
    # A failing verdict still lists all four rules in R1-R4 order.
    h = history([snap("2026-11-02T10:00+01:00", "100000", "89000")])
    verdict = evaluate(h, config)
    assert [r.rule for r in verdict.rules] == RULE_ORDER


# --- FAILED with the earliest breach time when several rules failed ---


def test_single_failed_rule_gives_failed_with_its_breach_time(config):
    # Equity 94,000 is below the daily limit of 95,000 but above the 90,000 floor:
    # only R1 fails, so failed_at is R1's breach time.
    h = history([snap("2026-11-02T10:00+01:00", "100000", "94000")])
    verdict = evaluate(h, config)
    assert verdict.status is VerdictStatus.FAILED
    assert verdict.failed_at == datetime.fromisoformat("2026-11-02T10:00+01:00")
    assert verdict.rules[0].status is RuleStatus.FAILED
    assert verdict.rules[1].status is RuleStatus.PASSED


def test_several_failures_report_earliest_when_r1_breaches_first(config):
    # 10:00 equity 94,000 breaches R1 (limit 95,000); 11:00 equity 89,000 breaches R2
    # (floor 90,000). Earliest breach is 10:00.
    h = history(
        [
            snap("2026-11-02T10:00+01:00", "100000", "94000"),
            snap("2026-11-02T11:00+01:00", "100000", "89000"),
        ]
    )
    verdict = evaluate(h, config)
    assert verdict.status is VerdictStatus.FAILED
    assert verdict.rules[0].status is RuleStatus.FAILED
    assert verdict.rules[1].status is RuleStatus.FAILED
    assert verdict.failed_at == datetime.fromisoformat("2026-11-02T10:00+01:00")


def test_several_failures_report_earliest_even_when_it_is_not_the_first_rule(config):
    # Losses of 4,000/day keep R1 safe until day 3. Day 3 starts at balance 92,000,
    # so R1's limit is 87,000. At 10:00 equity 89,000 breaches only R2 (floor 90,000);
    # at 11:00 equity 86,000 also breaches R1. Earliest breach is R2's at 10:00,
    # even though R1 comes first in the list.
    h = history(
        [
            snap("2026-11-02T17:00+01:00", "96000"),
            snap("2026-11-03T17:00+01:00", "92000"),
            snap("2026-11-04T10:00+01:00", "92000", "89000"),
            snap("2026-11-04T11:00+01:00", "92000", "86000"),
        ]
    )
    verdict = evaluate(h, config)
    assert verdict.status is VerdictStatus.FAILED
    assert verdict.rules[0].breached_at == datetime.fromisoformat("2026-11-04T11:00+01:00")
    assert verdict.rules[1].breached_at == datetime.fromisoformat("2026-11-04T10:00+01:00")
    assert verdict.failed_at == datetime.fromisoformat("2026-11-04T10:00+01:00")


def test_earliest_breach_compares_instants_across_offsets(config):
    # Same scenario as above but R2's breach snapshot is written in UTC: 09:00Z is
    # 10:00 Prague, still earlier than R1's breach at 11:00 Prague.
    h = history(
        [
            snap("2026-11-02T17:00+01:00", "96000"),
            snap("2026-11-03T17:00+01:00", "92000"),
            snap("2026-11-04T09:00+00:00", "92000", "89000"),
            snap("2026-11-04T11:00+01:00", "92000", "86000"),
        ]
    )
    verdict = evaluate(h, config)
    assert verdict.failed_at == datetime.fromisoformat("2026-11-04T10:00+01:00")


def test_failure_wins_even_if_target_and_days_are_met(config):
    # R1 breached on day 1 (94,000 < 95,000), later the balance reaches 110,000 and
    # there are 4 trading days. Any failed rule makes the verdict FAILED.
    h = history(
        [
            snap("2026-11-02T10:00+01:00", "100000", "94000"),
            snap("2026-11-05T17:00+01:00", "110000"),
        ],
        trades_on_days(2, 3, 4, 5),
    )
    verdict = evaluate(h, config)
    assert verdict.rules[2].status is RuleStatus.PASSED
    assert verdict.rules[3].status is RuleStatus.PASSED
    assert verdict.status is VerdictStatus.FAILED
    assert verdict.failed_at == datetime.fromisoformat("2026-11-02T10:00+01:00")


# --- PASSED only when every rule passed ---


def test_all_rules_passed_gives_passed(config):
    # Balance climbs to exactly 110,000 (target is "at or above") over 4 trading days,
    # with no drawdown: all four rules pass.
    h = history(
        [
            snap("2026-11-02T17:00+01:00", "102000"),
            snap("2026-11-03T17:00+01:00", "105000"),
            snap("2026-11-04T17:00+01:00", "108000"),
            snap("2026-11-05T17:00+01:00", "110000"),
        ],
        trades_on_days(2, 3, 4, 5),
    )
    verdict = evaluate(h, config)
    assert [r.status for r in verdict.rules] == [RuleStatus.PASSED] * 4
    assert verdict.status is VerdictStatus.PASSED
    assert verdict.failed_at is None


# --- IN_PROGRESS otherwise ---


def test_no_activity_is_in_progress(config):
    # No snapshots or trades: R1/R2 pass, R3/R4 pending, nothing failed.
    verdict = evaluate(history(), config)
    assert verdict.status is VerdictStatus.IN_PROGRESS
    assert verdict.failed_at is None


def test_target_met_but_too_few_days_is_in_progress(config):
    # Balance 110,000 meets R3, but only 3 trading days (R4 needs 4): pending, not passed.
    h = history([snap("2026-11-04T17:00+01:00", "110000")], trades_on_days(2, 3, 4))
    verdict = evaluate(h, config)
    assert verdict.rules[2].status is RuleStatus.PASSED
    assert verdict.rules[3].status is RuleStatus.PENDING
    assert verdict.status is VerdictStatus.IN_PROGRESS
    assert verdict.failed_at is None


def test_days_met_but_target_not_reached_is_in_progress(config):
    # 4 trading days but balance 109,999.99 is just short of 110,000: R3 pending.
    h = history([snap("2026-11-05T17:00+01:00", "109999.99")], trades_on_days(2, 3, 4, 5))
    verdict = evaluate(h, config)
    assert verdict.rules[2].status is RuleStatus.PENDING
    assert verdict.rules[3].status is RuleStatus.PASSED
    assert verdict.status is VerdictStatus.IN_PROGRESS


def test_floating_profit_does_not_pass_the_challenge(config):
    # Equity 115,000 but balance 105,000: R3 uses balance, so the target is not met.
    h = history(
        [snap("2026-11-05T17:00+01:00", "105000", "115000")],
        trades_on_days(2, 3, 4, 5),
    )
    verdict = evaluate(h, config)
    assert verdict.status is VerdictStatus.IN_PROGRESS


def test_equity_exactly_at_limits_is_not_a_failure(config):
    # Equity exactly 95,000 is at R1's daily limit (allowed), not below it, so the
    # verdict is IN_PROGRESS rather than FAILED.
    h = history([snap("2026-11-02T10:00+01:00", "100000", "95000")], trades_on_days(2))
    verdict = evaluate(h, config)
    assert verdict.status is VerdictStatus.IN_PROGRESS
    assert verdict.failed_at is None
