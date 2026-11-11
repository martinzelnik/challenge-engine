from datetime import datetime
from decimal import Decimal

from conftest import snap

from challenge_engine import AccountHistory, ChallengeConfig, RuleStatus
from challenge_engine.rules import check_max_loss

# Spec R2: limit = initial_balance * (1 - max_loss_pct). With the default config
# (100,000 initial, 10%) the limit is a fixed 90,000. Breach = first snapshot whose
# EQUITY is STRICTLY below the limit.


def run(config, *snapshots):
    return check_max_loss(AccountHistory(snapshots=snapshots), config)


# --- AC1: No breach gives PASSED -------------------------------------------------


def test_no_activity_passes(config):
    # No snapshots means no snapshot can be below the limit, so nothing is breached -> PASSED.
    result = run(config)
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None


def test_loss_within_limit_passes(config):
    # Lowest equity 92,500 is above the 90,000 limit -> no breach -> PASSED, rule "max_loss".
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "95000"),
        snap("2026-11-02T11:00+01:00", "100000", "92500"),
    )
    assert result.status is RuleStatus.PASSED
    assert result.rule == "max_loss"
    assert result.breached_at is None


def test_limit_is_not_raised_by_profits(config):
    # The limit depends only on initial_balance, not on peak balance/equity: after growing
    # to 108,000, dropping to 90,500 equity is still above 90,000 -> PASSED.
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "108000"),
        snap("2026-11-03T10:00+01:00", "108000", "90500"),
    )
    assert result.status is RuleStatus.PASSED


def test_rule_checks_equity_not_balance_when_balance_is_below(config):
    # Spec says equity is compared. Balance 89,000 is below the limit but equity 91,000
    # (floating profit) is above it -> not a breach -> PASSED.
    result = run(config, snap("2026-11-02T10:00+01:00", "89000", "91000"))
    assert result.status is RuleStatus.PASSED


# --- AC2: Equity strictly below the overall limit gives FAILED + first breach time --


def test_one_cent_below_limit_fails(config):
    # 89,999.99 < 90,000 strictly -> FAILED at that snapshot's time.
    result = run(config, snap("2026-11-02T10:00+01:00", "100000", "89999.99"))
    assert result.status is RuleStatus.FAILED
    assert result.rule == "max_loss"
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:00+01:00")


def test_breach_reports_first_breach_time(config):
    # The first snapshot below 90,000 is at 10:05; later (deeper) breaches don't change
    # breached_at because the spec says "the first snapshot".
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "91000"),
        snap("2026-11-02T10:05+01:00", "100000", "89900"),
        snap("2026-11-02T10:10+01:00", "100000", "85000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:05+01:00")
    # Detail should explain with the numbers: the limit and the breaching equity.
    assert "90,000.00" in result.detail
    assert "89,900.00" in result.detail


def test_recovery_after_breach_still_fails(config):
    # Once equity dipped strictly below 90,000 the rule is breached; recovering later
    # does not undo it -> FAILED at the dip's time.
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "89000"),
        snap("2026-11-03T10:00+01:00", "100000", "101000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:00+01:00")


def test_floating_loss_breaches_even_with_balance_above_limit(config):
    # Balance 100,000 (no closed losses) but equity 89,000 from open positions: R2 uses
    # equity, so this is a breach.
    result = run(config, snap("2026-11-02T10:00+01:00", "100000", "89000"))
    assert result.status is RuleStatus.FAILED


def test_losses_accumulate_across_days(config):
    # -4,000 per day never breaks the 5% daily rule, but the overall limit is fixed at
    # 90,000: day 3 equity 88,000 < 90,000 -> FAILED at the day-3 snapshot.
    result = run(
        config,
        snap("2026-11-02T17:00+01:00", "96000"),
        snap("2026-11-03T17:00+01:00", "92000"),
        snap("2026-11-04T17:00+01:00", "88000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-04T17:00+01:00")


def test_breached_at_is_the_snapshot_time_in_any_offset(config):
    # breached_at is "the snapshot time of the first breach"; a UTC-stamped snapshot
    # must be reported as the same instant.
    result = run(config, snap("2026-11-02T23:30+00:00", "100000", "80000"))
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-03T00:30+01:00")


# --- AC3: Equity exactly at the limit is allowed -----------------------------------


def test_equity_exactly_at_limit_is_allowed(config):
    # 90,000 is not strictly below 90,000 -> no breach -> PASSED.
    result = run(config, snap("2026-11-02T10:00+01:00", "100000", "90000"))
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None


def test_equity_at_limit_with_trailing_zeros_is_allowed(config):
    # Decimal("90000.00") equals 90,000, so it is still exactly at the limit -> PASSED.
    result = run(config, snap("2026-11-02T10:00+01:00", "90000.00", "90000.00"))
    assert result.status is RuleStatus.PASSED


def test_uses_configured_percentage_and_initial_balance():
    # Limit = 50,000 * (1 - 0.08) = 46,000: exactly at it passes, 1 below fails.
    config = ChallengeConfig(initial_balance=Decimal("50000"), max_loss_pct=Decimal("0.08"))
    assert run(config, snap("2026-11-02T10:00+01:00", "50000", "46000")).status is (
        RuleStatus.PASSED
    )
    assert run(config, snap("2026-11-02T10:00+01:00", "50000", "45999")).status is (
        RuleStatus.FAILED
    )
