from conftest import snap

from challenge_engine import AccountHistory, RuleStatus, check_profit_target


def test_no_snapshots_is_pending(config):
    result = check_profit_target(AccountHistory(), config)
    assert result.status is RuleStatus.PENDING


def test_balance_at_target_passes(config):
    history = AccountHistory(snapshots=(snap("2026-11-05T16:00+01:00", "110000"),))
    result = check_profit_target(history, config)
    assert result.status is RuleStatus.PASSED
    assert "110,000.00" in result.detail


def test_floating_profit_does_not_count(config):
    history = AccountHistory(
        snapshots=(snap("2026-11-05T16:00+01:00", balance="105000", equity="112000"),)
    )
    result = check_profit_target(history, config)
    assert result.status is RuleStatus.PENDING
    assert "5,000.00 short" in result.detail


def test_uses_latest_snapshot(config):
    history = AccountHistory(
        snapshots=(
            snap("2026-11-04T16:00+01:00", "111000"),
            snap("2026-11-05T16:00+01:00", "108000"),
        )
    )
    assert check_profit_target(history, config).status is RuleStatus.PENDING
