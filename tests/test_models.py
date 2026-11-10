from datetime import datetime
from decimal import Decimal

import pytest
from conftest import snap

from challenge_engine import AccountHistory, ChallengeConfig, EquitySnapshot, Trade


def test_snapshot_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        EquitySnapshot(at=datetime(2026, 11, 2, 10), balance=Decimal(1), equity=Decimal(1))


def test_trade_rejects_close_before_open():
    with pytest.raises(ValueError, match="before opened_at"):
        Trade(
            symbol="EURUSD",
            opened_at=datetime.fromisoformat("2026-11-02T10:00+01:00"),
            closed_at=datetime.fromisoformat("2026-11-02T09:00+01:00"),
            pnl=Decimal("10"),
        )


def test_history_rejects_unsorted_snapshots():
    with pytest.raises(ValueError, match="sorted"):
        AccountHistory(
            snapshots=(
                snap("2026-11-02T11:00+01:00", "100000"),
                snap("2026-11-02T10:00+01:00", "100000"),
            )
        )


def test_config_rejects_unknown_timezone():
    with pytest.raises(Exception):  # noqa: B017 - zoneinfo raises its own error type
        ChallengeConfig(initial_balance=Decimal("100000"), timezone="Mars/Olympus")


def test_config_rejects_non_positive_balance():
    with pytest.raises(ValueError):
        ChallengeConfig(initial_balance=Decimal("0"))
