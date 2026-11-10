# AGENTS.md

Instructions for any coding agent (Claude Code, Codex, Cursor, …) working in this repo.
Humans should read it too: it is the contract between the people and the agents.

## What this project is

`challenge-engine` checks whether a trader's account passed a prop-trading challenge.
It reads the account's equity snapshots and trades and decides, for each rule,
whether it passed, failed or is still pending. The full rules are in
[docs/spec.md](docs/spec.md). **The spec is the source of truth.** If code and
spec disagree, the code is wrong.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # first-time setup
.venv/bin/pytest                    # run tests
.venv/bin/ruff check .              # lint
.venv/bin/ruff format .             # format
```

All three must pass before you commit.

## Layout

| Path | What it is | Who edits it |
|---|---|---|
| `src/challenge_engine/` | The engine | Agents |
| `tests/` | Visible tests | Agents |
| `docs/spec.md` | Rule spec | Humans only |
| `feature_list.json` | Features and whether they pass | Agents may only flip `passes` |
| `progress.md` | Running log of sessions | Agents append |
| `evals/` | Eval tasks, hidden tests, scorer | Humans only |

## Session workflow

Every session works on **one feature** from start to finish:

1. Read `progress.md` (latest entries first) and `git log --oneline -10` to see where things stand.
2. Run the tests. If something is already broken, fix that first and log it.
3. Pick the first feature in `feature_list.json` with `"passes": false`.
4. Write tests for its acceptance criteria first, then the code.
5. Run tests, lint and format. All green.
6. Set that feature's `"passes"` to `true`. Never change any other field.
7. Append an entry to `progress.md` (template at the top of that file).
8. Commit with a message like `feat(F-002): max daily loss rule`.

Stop after one feature. Small, reviewable commits beat one big one.

## Code conventions

- **Money is `Decimal`, never `float`.** Build it from strings: `Decimal("0.05")`.
- **Datetimes must be timezone-aware.** The models reject naive datetimes. Trading
  days are computed in `ChallengeConfig.timezone`, not UTC.
- **Rules are pure functions** `check_x(history, config) -> RuleResult`. No I/O,
  no clock reads, no global state. This keeps them easy to test.
- Each `RuleResult.detail` explains *why* in one sentence a support agent could
  paste to a trader, with the numbers.
- Type hints everywhere. No new dependencies without asking.

## Boundaries

- Do not read or edit anything under `evals/tasks/*/hidden_tests/`. Those tests
  measure you; reading them invalidates the eval.
- Do not edit `docs/spec.md`. If the spec is ambiguous, write the question in
  `progress.md` under "Open questions" and pick the stricter reading.
- Do not weaken or delete an existing test to make your change pass.
- Never commit secrets, `.env` files or real trader data.

## Definition of done

Tests pass, lint passes, the feature's acceptance criteria in `feature_list.json`
each have a test, `progress.md` is updated, and the commit contains only this feature.
