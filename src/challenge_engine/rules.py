"""Challenge rules. Each rule is a pure function; see docs/spec.md."""

from __future__ import annotations

from datetime import date

from .models import AccountHistory, ChallengeConfig, RuleResult, RuleStatus, Verdict


def check_max_daily_loss(history: AccountHistory, config: ChallengeConfig) -> RuleResult:
    """R1. Equity must not fall below the day-start balance minus the daily allowance.

    The day-start balance is the closed balance of the last snapshot before local
    midnight (or the initial balance on the first day). Days are split in config.timezone.
    """
    allowance = config.max_daily_loss_pct * config.initial_balance
    current_day: date | None = None
    day_start_balance = config.initial_balance
    previous_balance = config.initial_balance

    for snapshot in history.snapshots:
        day = snapshot.at.astimezone(config.tz).date()
        if day != current_day:
            current_day = day
            day_start_balance = previous_balance
        limit = day_start_balance - allowance
        if snapshot.equity < limit:
            return RuleResult(
                "max_daily_loss",
                RuleStatus.FAILED,
                f"On {day.isoformat()} equity fell to {snapshot.equity:,.2f}, below the daily "
                f"limit of {limit:,.2f} (day-start balance {day_start_balance:,.2f} minus "
                f"{allowance:,.2f}).",
                breached_at=snapshot.at,
            )
        previous_balance = snapshot.balance

    return RuleResult(
        "max_daily_loss",
        RuleStatus.PASSED,
        f"Equity never fell more than {allowance:,.2f} below any day's starting balance.",
    )


def check_max_loss(history: AccountHistory, config: ChallengeConfig) -> RuleResult:
    """R2. Equity must never fall strictly below initial_balance * (1 - max_loss_pct).

    The limit is fixed: profits do not raise it. The first breaching snapshot fails the rule.
    """
    limit = config.initial_balance * (1 - config.max_loss_pct)

    for snapshot in history.snapshots:
        if snapshot.equity < limit:
            return RuleResult(
                "max_loss",
                RuleStatus.FAILED,
                f"Equity fell to {snapshot.equity:,.2f}, below the overall loss limit of "
                f"{limit:,.2f} ({config.max_loss_pct:.2%} of the initial balance of "
                f"{config.initial_balance:,.2f}).",
                breached_at=snapshot.at,
            )

    if not history.snapshots:
        return RuleResult(
            "max_loss",
            RuleStatus.PASSED,
            f"No account activity yet; the overall loss limit is {limit:,.2f}.",
        )

    lowest = min(snapshot.equity for snapshot in history.snapshots)
    return RuleResult(
        "max_loss",
        RuleStatus.PASSED,
        f"Lowest equity was {lowest:,.2f}, at or above the overall loss limit of {limit:,.2f}.",
    )


def check_profit_target(history: AccountHistory, config: ChallengeConfig) -> RuleResult:
    """R3. Met when the latest closed balance reaches the target. Floating profit doesn't count."""
    target = config.initial_balance * (1 + config.profit_target_pct)
    if not history.snapshots:
        return RuleResult("profit_target", RuleStatus.PENDING, "No account activity yet.")

    balance = history.snapshots[-1].balance
    if balance >= target:
        return RuleResult(
            "profit_target",
            RuleStatus.PASSED,
            f"Balance {balance:,.2f} reached the profit target of {target:,.2f}.",
        )
    return RuleResult(
        "profit_target",
        RuleStatus.PENDING,
        f"Balance {balance:,.2f} is {target - balance:,.2f} short of the target of {target:,.2f}.",
    )


def check_min_trading_days(history: AccountHistory, config: ChallengeConfig) -> RuleResult:
    """R4. Feature F-004."""
    raise NotImplementedError("F-004")


def evaluate(history: AccountHistory, config: ChallengeConfig) -> Verdict:
    """Overall verdict. Feature F-005."""
    raise NotImplementedError("F-005")
