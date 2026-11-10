# Challenge rules spec

The source of truth for the engine. Agents must not edit this file.

## Inputs

- `ChallengeConfig`: the challenge's parameters.

  | Field | Default | Meaning |
  |---|---|---|
  | `initial_balance` | (required) | Starting balance, e.g. `Decimal("100000")` |
  | `max_daily_loss_pct` | `0.05` | Max loss allowed within one trading day, as a share of the initial balance |
  | `max_loss_pct` | `0.10` | Max loss allowed overall, as a share of the initial balance |
  | `profit_target_pct` | `0.10` | Profit needed to pass, as a share of the initial balance |
  | `min_trading_days` | `4` | Distinct trading days needed to pass |
  | `timezone` | `Europe/Prague` | Timezone that defines when a trading day starts (midnight) |

- `AccountHistory`: what happened on the account.
  - `snapshots`: `EquitySnapshot(at, balance, equity)`, sorted by `at`.
    `balance` includes closed trades only; `equity` = balance + floating P&L of open positions.
  - `trades`: `Trade(symbol, opened_at, closed_at, pnl)`. `closed_at` is `None` for open trades.

All datetimes are timezone-aware. All money is `Decimal`.

## Rules

Each rule is a pure function `check_<rule>(history, config) -> RuleResult`
in `challenge_engine.rules`. `RuleResult` has `rule`, `status`
(`PASSED` / `FAILED` / `PENDING`), `detail` (one human-readable sentence) and
`breached_at` (the snapshot time of the first breach, or `None`).

### R1 Max daily loss — `check_max_daily_loss`

- A trading day runs from midnight to midnight in `config.timezone`.
- **Day-start balance** = `balance` of the last snapshot strictly before that
  day's midnight. If there is none, it is `initial_balance`.
- **Daily limit** = day-start balance − `max_daily_loss_pct` × `initial_balance`.
- The rule is breached at the first snapshot whose `equity` is **strictly
  below** that day's limit. Equity exactly at the limit is allowed.
- Status: `FAILED` with `breached_at` if breached, otherwise `PASSED`.

### R2 Max loss — `check_max_loss`

- **Limit** = `initial_balance` × (1 − `max_loss_pct`).
- Breached at the first snapshot whose `equity` is strictly below the limit.
- Status: `FAILED` with `breached_at` if breached, otherwise `PASSED`.

### R3 Profit target — `check_profit_target`

- **Target** = `initial_balance` × (1 + `profit_target_pct`).
- Met when the latest snapshot's `balance` (not equity) is at or above the target.
  Floating profit does not count.
- Status: `PASSED` if met, otherwise `PENDING`. With no snapshots: `PENDING`.

### R4 Minimum trading days — `check_min_trading_days`

- A trading day counts if at least one trade was **opened** on it (in `config.timezone`).
- Status: `PASSED` if the count ≥ `min_trading_days`, otherwise `PENDING`.

## Verdict — `evaluate(history, config) -> Verdict`

- Runs all four rules and returns them in the order R1–R4.
- `FAILED` if any rule failed. If several failed, `failed_at` is the earliest `breached_at`.
- `PASSED` if no rule failed and all rules passed.
- `IN_PROGRESS` otherwise.
