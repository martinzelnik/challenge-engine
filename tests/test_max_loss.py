from datetime import datetime
from decimal import Decimal

from conftest import snap

from challenge_engine import AccountHistory, ChallengeConfig, RuleStatus, check_max_loss

# With the default config (100,000 initial, 10%) the overall limit is 90,000.


def run(config, *snapshots):
    return check_max_loss(AccountHistory(snapshots=snapshots), config)


# --- No breach gives PASSED ---


def test_no_activity_passes(config):
    # No snapshots means equity never went below the limit.
    result = run(config)
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None


def test_loss_within_limit_passes(config):
    # 92,000 is above the 90,000 limit.
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "95000"),
        snap("2026-11-03T10:00+01:00", "93000", "92000"),
    )
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None
    assert result.rule == "max_loss"


def test_losses_spread_over_several_days_add_up(config):
    # -4,000 per day never breaches R1, but by day 3 equity is 88,000, below 90,000.
    result = run(
        config,
        snap("2026-11-02T17:00+01:00", "96000"),
        snap("2026-11-03T17:00+01:00", "92000"),
        snap("2026-11-04T17:00+01:00", "88000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-04T17:00+01:00")


def test_limit_is_fixed_on_initial_balance_not_trailing(config):
    # Profit to 120,000, then drop to 91,000: a 29,000 drawdown from the peak, but the
    # limit is initial_balance x 0.9 = 90,000, so this still passes.
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "120000"),
        snap("2026-11-03T10:00+01:00", "120000", "91000"),
    )
    assert result.status is RuleStatus.PASSED


# --- Equity strictly below the limit gives FAILED with the first breach time ---


def test_breach_reports_first_breach_time(config):
    # 89,500 at 10:05 is the first equity below 90,000; later breaches don't move it.
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "91000"),
        snap("2026-11-02T10:05+01:00", "100000", "89500"),
        snap("2026-11-02T10:10+01:00", "100000", "85000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:05+01:00")
    assert "90,000.00" in result.detail
    assert "89,500.00" in result.detail


def test_recovery_after_breach_still_fails(config):
    # Equity dips to 89,000 then recovers to 100,000; the breach already happened.
    result = run(
        config,
        snap("2026-11-02T10:00+01:00", "100000", "89000"),
        snap("2026-11-02T11:00+01:00", "100000"),
    )
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:00+01:00")


def test_uses_equity_not_balance(config):
    # Balance stays at 100,000 but floating loss puts equity at 89,000: breached.
    result = run(config, snap("2026-11-02T10:00+01:00", "100000", "89000"))
    assert result.status is RuleStatus.FAILED


def test_balance_below_limit_with_equity_above_passes(config):
    # Balance is 89,000 but open profit lifts equity to 90,500; only equity counts.
    result = run(config, snap("2026-11-02T10:00+01:00", "89000", "90500"))
    assert result.status is RuleStatus.PASSED


# --- Equity exactly at the limit is allowed ---


def test_equity_exactly_at_limit_is_allowed(config):
    # "Strictly below" 90,000 is a breach; exactly 90,000 is not.
    result = run(config, snap("2026-11-02T10:00+01:00", "95000", "90000"))
    assert result.status is RuleStatus.PASSED
    assert result.breached_at is None


def test_one_cent_below_limit_fails(config):
    # 89,999.99 is strictly below 90,000.
    result = run(config, snap("2026-11-02T10:00+01:00", "95000", "89999.99"))
    assert result.status is RuleStatus.FAILED
    assert result.breached_at == datetime.fromisoformat("2026-11-02T10:00+01:00")


def test_uses_configured_percentage():
    # Limit is 50,000 x (1 - 0.08) = 46,000.
    config = ChallengeConfig(initial_balance=Decimal("50000"), max_loss_pct=Decimal("0.08"))
    assert run(config, snap("2026-11-02T10:00+01:00", "50000", "46000")).status is (
        RuleStatus.PASSED
    )
    assert run(config, snap("2026-11-02T10:00+01:00", "50000", "45999")).status is (
        RuleStatus.FAILED
    )
