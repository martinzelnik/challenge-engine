from datetime import datetime
from decimal import Decimal

import pytest

from challenge_engine import ChallengeConfig, EquitySnapshot


def snap(iso: str, balance: str, equity: str | None = None) -> EquitySnapshot:
    """Snapshot from an ISO timestamp with offset, e.g. snap("2026-11-02T10:00+01:00", "100000")."""
    return EquitySnapshot(
        at=datetime.fromisoformat(iso),
        balance=Decimal(balance),
        equity=Decimal(equity if equity is not None else balance),
    )


@pytest.fixture
def config() -> ChallengeConfig:
    return ChallengeConfig(initial_balance=Decimal("100000"))
