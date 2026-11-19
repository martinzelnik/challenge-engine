# Evals

Measure how well a coding agent (and this harness) builds features, so changes to
prompts, AGENTS.md, models or tooling can be judged by numbers instead of vibes.

## How it works

Each task in `tasks/<id>/` has:
- `task.md`: the prompt given to the agent.
- `hidden_tests/`: tests the agent never sees (blocked in `.claude/settings.json`).

A run: copy the repo at a known commit, give the agent `task.md`, let it work,
then score the result:

```bash
.venv/bin/python evals/score.py F-002-max-daily-loss --workspace /path/to/run --label opus-v1
```

Each score is appended to `results/results.jsonl` with the task, label, commit,
tests passed and lint errors.

## Scenarios

`scenarios/*.json` are whole accounts with the verdict a person worked out from
[docs/spec.md](../docs/spec.md), the arithmetic written down in `worked_out`. They
are a regression check that does not come from the agents: the tests in `tests/`
are written by the same model that writes the code, so a misread spec would pass
both. CI runs them on every PR, through the real CLI and through `evaluate`:

```bash
.venv/bin/python evals/run_scenarios.py
```

To add one, copy a file, change the account, and work out the expected result by
hand from the spec before running the engine. Never copy the engine's output.

## Roadmap

- [x] Scorer: hidden tests + lint
- [ ] Runner: start the agent on a fresh copy of the repo (Claude Agent SDK), in a sandbox
- [ ] Record cost, tokens, wall time and diff size per run
- [ ] Tasks for F-003 to F-006, plus a few bug-fix tasks
- [ ] Compare models and harness versions; publish a table in the README
