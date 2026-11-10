# challenge-engine

A prop-trading challenge rule engine (daily loss, max loss, profit target,
trading days) built **agent-first**: coding agents write the code, and this
repo's harness tells them how, checks their work and measures them.

The engine is the small part. The interesting part is the harness around it.

## The harness

| Piece | Where |
|---|---|
| Agent instructions and boundaries | [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md) |
| Spec the agents build against | [docs/spec.md](docs/spec.md) |
| Feature list with pass/fail state | [feature_list.json](feature_list.json) |
| Session log, so any session can resume | [progress.md](progress.md) |
| Permission rules (no secrets, no peeking at evals) | [.claude/settings.json](.claude/settings.json) |
| Evals with hidden tests and a scorer | [evals/](evals/) |

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python evals/score.py F-002-max-daily-loss --no-save
```

## Roadmap

1. [x] Scaffold, spec, agent instructions, first eval task
2. [ ] Initializer + coding-agent loop on the Claude Agent SDK
3. [ ] Eval runner with cost, time and diff-size tracking
4. [ ] Sandbox (Docker, no network), permission hooks, per-run budget
5. [ ] Model routing and a model comparison
6. [ ] MCP server (spec + test results) and a CI hook for agent PR review
7. [ ] Write-up: what worked, what didn't, the numbers
