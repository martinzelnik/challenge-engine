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
| PR gate: tests, lint, agent boundaries, hidden evals | [.github/workflows/pr-review.yml](.github/workflows/pr-review.yml), [scripts/check_boundaries.py](scripts/check_boundaries.py) |
| Reviewer agent that approves or requests changes | [REVIEW.md](REVIEW.md), [scripts/submit_review.py](scripts/submit_review.py) |

### How a PR gets merged

1. **Checks** (no AI): tests, lint, the boundary check (an agent may only flip one
   feature's `passes`) and the hidden eval tests for that feature.
2. **Reviewer agent** (Claude): reviews the diff against the spec and REVIEW.md, leaves
   inline comments and returns a verdict. A script, not the model, decides what is
   submitted: it never approves a PR that changes harness files.
3. **A human merges.** Agents write and review the code; people own the harness
   and the merge.

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
6. [ ] MCP server (spec + test results); [x] CI reviewer agent for PRs
7. [ ] Write-up: what worked, what didn't, the numbers
