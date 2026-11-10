"""Data models. See docs/spec.md for what each field means."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware, got naive datetime {value!r}")


@dataclass(frozen=True)
class ChallengeConfig:
    initial_balance: Decimal
    max_daily_loss_pct: Decimal = Decimal("0.05")
    max_loss_pct: Decimal = Decimal("0.10")
    profit_target_pct: Decimal = Decimal("0.10")
    min_trading_days: int = 4
    timezone: str = "Europe/Prague"

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        ZoneInfo(self.timezone)  # raises if the timezone is unknown

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class EquitySnapshot:
    at: datetime
    balance: Decimal
    equity: Decimal

    def __post_init__(self) -> None:
        _require_aware("EquitySnapshot.at", self.at)


@dataclass(frozen=True)
class Trade:
    symbol: str
    opened_at: datetime
    closed_at: datetime | None
    pnl: Decimal

    def __post_init__(self) -> None:
        _require_aware("Trade.opened_at", self.opened_at)
        _require_aware("Trade.closed_at", self.closed_at)
        if self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("Trade.closed_at is before opened_at")


@dataclass(frozen=True)
class AccountHistory:
    snapshots: tuple[EquitySnapshot, ...] = ()
    trades: tuple[Trade, ...] = ()

    def __post_init__(self) -> None:
        times = [s.at for s in self.snapshots]
        if times != sorted(times):
            raise ValueError("snapshots must be sorted by time")


class RuleStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    PENDING = "PENDING"


@dataclass(frozen=True)
class RuleResult:
    rule: str
    status: RuleStatus
    detail: str
    breached_at: datetime | None = None


class VerdictStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus
    rules: tuple[RuleResult, ...] = field(default_factory=tuple)
    failed_at: datetime | None = None
