"""Command-line entry point: `python -m challenge_engine account.json`.

The account file is JSON that mirrors the models, with money as strings (never JSON
numbers, which would be parsed as floats) and ISO 8601 datetimes with offsets:

    {
      "config": {"initial_balance": "100000", ...optional ChallengeConfig fields},
      "snapshots": [{"at": "...", "balance": "...", "equity": "..."}],
      "trades": [{"symbol": "...", "opened_at": "...", "closed_at": "..." | null, "pnl": "..."}]
    }

Exit codes: 0 for PASSED or IN_PROGRESS, 1 for FAILED, 2 for invalid input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from .models import (
    AccountHistory,
    ChallengeConfig,
    EquitySnapshot,
    Trade,
    Verdict,
    VerdictStatus,
)
from .rules import evaluate

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2

_CONFIG_MONEY_FIELDS = (
    "initial_balance",
    "max_daily_loss_pct",
    "max_loss_pct",
    "profit_target_pct",
)
_CONFIG_FIELDS = frozenset((*_CONFIG_MONEY_FIELDS, "min_trading_days", "timezone"))
_SNAPSHOT_FIELDS = frozenset(("at", "balance", "equity"))
_TRADE_FIELDS = frozenset(("symbol", "opened_at", "closed_at", "pnl"))
_TOP_LEVEL_FIELDS = frozenset(("config", "snapshots", "trades"))


class InputError(Exception):
    """The account file is not valid input. The message is shown to the user."""


def _object(value: Any, where: str, fields: frozenset[str], required: frozenset[str]) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{where} must be a JSON object")
    missing = sorted(required - value.keys())
    if missing:
        raise InputError(f"{where} is missing {', '.join(missing)}")
    unknown = sorted(value.keys() - fields)
    if unknown:
        raise InputError(f"{where} has unknown field(s) {', '.join(unknown)}")
    return value


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise InputError(f"{where} must be a JSON array")
    return value


def _decimal(value: Any, where: str) -> Decimal:
    if not isinstance(value, str):
        raise InputError(f"{where} must be a decimal number written as a string, got {value!r}")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise InputError(f"{where} is not a number: {value!r}") from None
    if not result.is_finite():
        raise InputError(f"{where} must be a finite number, got {value!r}")
    return result


def _datetime(value: Any, where: str) -> datetime:
    if not isinstance(value, str):
        raise InputError(f"{where} must be an ISO 8601 datetime string, got {value!r}")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise InputError(f"{where} is not an ISO 8601 datetime: {value!r}") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise InputError(f"{where} must include a timezone offset, got {value!r}")
    return result


def _parse_config(value: Any) -> ChallengeConfig:
    data = _object(value, "config", _CONFIG_FIELDS, frozenset(("initial_balance",)))
    kwargs: dict[str, Any] = {
        name: _decimal(data[name], f"config.{name}")
        for name in _CONFIG_MONEY_FIELDS
        if name in data
    }
    if "min_trading_days" in data:
        days = data["min_trading_days"]
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise InputError(
                f"config.min_trading_days must be a non-negative integer, got {days!r}"
            )
        kwargs["min_trading_days"] = days
    if "timezone" in data:
        tz = data["timezone"]
        if not isinstance(tz, str):
            raise InputError(f"config.timezone must be a string, got {tz!r}")
        kwargs["timezone"] = tz
    try:
        return ChallengeConfig(**kwargs)
    except ZoneInfoNotFoundError:
        raise InputError(
            f"config.timezone is not a known timezone: {kwargs['timezone']!r}"
        ) from None
    except ValueError as exc:
        raise InputError(f"config is invalid: {exc}") from None


def _parse_snapshot(value: Any, where: str) -> EquitySnapshot:
    data = _object(value, where, _SNAPSHOT_FIELDS, _SNAPSHOT_FIELDS)
    return EquitySnapshot(
        at=_datetime(data["at"], f"{where}.at"),
        balance=_decimal(data["balance"], f"{where}.balance"),
        equity=_decimal(data["equity"], f"{where}.equity"),
    )


def _parse_trade(value: Any, where: str) -> Trade:
    data = _object(value, where, _TRADE_FIELDS, _TRADE_FIELDS)
    symbol = data["symbol"]
    if not isinstance(symbol, str) or not symbol:
        raise InputError(f"{where}.symbol must be a non-empty string, got {symbol!r}")
    closed_at = data["closed_at"]
    try:
        return Trade(
            symbol=symbol,
            opened_at=_datetime(data["opened_at"], f"{where}.opened_at"),
            closed_at=None if closed_at is None else _datetime(closed_at, f"{where}.closed_at"),
            pnl=_decimal(data["pnl"], f"{where}.pnl"),
        )
    except ValueError as exc:
        raise InputError(f"{where} is invalid: {exc}") from None


def parse_account(data: Any) -> tuple[AccountHistory, ChallengeConfig]:
    """Build the config and history from decoded JSON, raising InputError if invalid."""
    top = _object(data, "the account file", _TOP_LEVEL_FIELDS, _TOP_LEVEL_FIELDS)
    config = _parse_config(top["config"])
    snapshots = tuple(
        _parse_snapshot(s, f"snapshots[{i}]")
        for i, s in enumerate(_list(top["snapshots"], "snapshots"))
    )
    trades = tuple(
        _parse_trade(t, f"trades[{i}]") for i, t in enumerate(_list(top["trades"], "trades"))
    )
    try:
        history = AccountHistory(snapshots=snapshots, trades=trades)
    except ValueError as exc:
        raise InputError(str(exc)) from None
    return history, config


def load_account(path: Path) -> tuple[AccountHistory, ChallengeConfig]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"{path}: file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"{path}: cannot read file ({exc})") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: not valid JSON ({exc})") from None
    try:
        return parse_account(data)
    except InputError as exc:
        raise InputError(f"{path}: {exc}") from None


def format_verdict(verdict: Verdict) -> str:
    lines = [f"Verdict: {verdict.status}"]
    if verdict.failed_at is not None:
        lines[0] += f" (first breach at {verdict.failed_at.isoformat()})"
    lines.extend(f"  {rule.rule}: {rule.status} - {rule.detail}" for rule in verdict.rules)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m challenge_engine",
        description="Evaluate a prop-trading challenge account from a JSON file.",
    )
    parser.add_argument("account", type=Path, help="path to the account JSON file")
    args = parser.parse_args(argv)  # exits with code 2 and a usage message on bad arguments

    try:
        history, config = load_account(args.account)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID

    verdict = evaluate(history, config)
    print(format_verdict(verdict))
    return EXIT_FAILED if verdict.status is VerdictStatus.FAILED else EXIT_OK
