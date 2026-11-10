"""Challenge rules. Each rule is a pure function; see docs/spec.md."""

from __future__ import annotations

from .models import AccountHistory, ChallengeConfig, RuleResult, RuleStatus, Verdict


def check_max_daily_loss(history: AccountHistory, config: ChallengeConfig) -> RuleResult:
    """R1. Feature F-002."""
    raise NotImplementedError("F-002")


def check_max_loss(history: AccountHistory, config: ChallengeConfig) -> RuleResult:
    """R2. Feature F-003."""
    raise NotImplementedError("F-003")


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
