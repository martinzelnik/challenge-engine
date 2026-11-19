"""Challenge rules. Each rule is a pure function; see docs/spec.md."""

from __future__ import annotations

from datetime import date

from .models import AccountHistory, ChallengeConfig, RuleResult, RuleStatus, Verdict, VerdictStatus


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
    """R2. Equity must never fall below a fixed floor of initial balance x (1 - max_loss_pct).

    The floor is static: it does not trail profits.
    """
    limit = config.initial_balance * (1 - config.max_loss_pct)

    for snapshot in history.snapshots:
        if snapshot.equity < limit:
            return RuleResult(
                "max_loss",
                RuleStatus.FAILED,
                f"Equity fell to {snapshot.equity:,.2f} at {snapshot.at.isoformat()}, below the "
                f"overall limit of {limit:,.2f} (initial balance {config.initial_balance:,.2f} "
                f"minus {config.initial_balance - limit:,.2f}).",
                breached_at=snapshot.at,
            )

    return RuleResult(
        "max_loss",
        RuleStatus.PASSED,
        f"Equity never fell below the overall limit of {limit:,.2f}.",
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
    """R4. Counts distinct local days (config.timezone) on which at least one trade was opened."""
    days = {trade.opened_at.astimezone(config.tz).date() for trade in history.trades}
    count = len(days)
    required = config.min_trading_days
    if count >= required:
        return RuleResult(
            "min_trading_days",
            RuleStatus.PASSED,
            f"Traded on {count} distinct days, meeting the minimum of {required}.",
        )
    return RuleResult(
        "min_trading_days",
        RuleStatus.PENDING,
        f"Traded on {count} distinct days so far; at least {required} are needed to pass.",
    )


def evaluate(history: AccountHistory, config: ChallengeConfig) -> Verdict:
    """Overall verdict: run R1-R4 in order and combine their statuses."""
    results = (
        check_max_daily_loss(history, config),
        check_max_loss(history, config),
        check_profit_target(history, config),
        check_min_trading_days(history, config),
    )
    failed = [r for r in results if r.status is RuleStatus.FAILED]
    if failed:
        breaches = [r.breached_at for r in failed if r.breached_at is not None]
        # Aware datetimes compare as instants, so mixed UTC offsets are handled.
        failed_at = min(breaches) if breaches else None
        return Verdict(VerdictStatus.FAILED, results, failed_at)
    if all(r.status is RuleStatus.PASSED for r in results):
        return Verdict(VerdictStatus.PASSED, results)
    return Verdict(VerdictStatus.IN_PROGRESS, results)
