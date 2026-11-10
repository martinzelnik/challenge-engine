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

## Roadmap

- [x] Scorer: hidden tests + lint
- [ ] Runner: start the agent on a fresh copy of the repo (Claude Agent SDK), in a sandbox
- [ ] Record cost, tokens, wall time and diff size per run
- [ ] Tasks for F-003 to F-006, plus a few bug-fix tasks
- [ ] Compare models and harness versions; publish a table in the README
