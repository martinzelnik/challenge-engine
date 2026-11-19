# Progress log

Newest entry at the top. Each session adds one entry using this template:

```
## YYYY-MM-DD — F-xxx <title>
- Agent/model:
- Done:
- Tests: <passed>/<total>, lint clean: yes/no
- Problems / surprises:
- Next:
```

## Open questions

(Spec ambiguities go here, with the reading you chose.)

- R1: does a snapshot at exactly local midnight belong to the old or new day?
  Chose the new day (a day runs from midnight inclusive), so its start balance
  is the last balance before midnight.

---

## 2026-11-19 — Tooling: agent pipeline dashboard (not a feature)
- Agent/model: Claude Code (project thread), at Martin's request
- Done: `observe/`, a local stdlib web dashboard: feature stages, live run log
  tail, run ledger (merged with unmerged branches' ledgers), denials summary,
  transcripts by session id, start/stop a run. `logs/runs/` is gitignored so a
  dashboard-started run doesn't dirty the tree.
- Tests: full suite green, lint clean: yes (tests/test_observe.py)
- Problems / surprises: hosted runs log `cost_usd` from `list_cost.amount`, which
  looks like cents ($29.00 for a test-writer whose local runs cost ~$0.45). The
  dashboard flags those and leaves them out of spend; the fix belongs in
  harness/managed/run.py (humans only).
- Next: Martin to decide on the hosted cost unit fix.

## 2026-11-19 — F-005 Overall verdict (evaluate)
- Agent/model: agent pipeline: test-writer (claude-opus-5-5, 15 turns, $0.43, 58s); implementer (claude-opus-5-5, 17 turns, $0.27, 30s)
- Done: test-writer wrote failing tests from the spec; implementer made them pass.
- Tests: 144 passed in 1.50s, lint clean: yes
- Problems / surprises: see the agents' summaries in the run log (logs/agent-runs.jsonl).
- Next: review the PR

## 2026-11-19 — F-004 Minimum trading days rule (R4)
- Agent/model: agent pipeline: test-writer (claude-opus-5-5, 14 turns, $0.39, 57s); implementer (claude-opus-5-5, 12 turns, $0.20, 22s)
- Done: test-writer wrote failing tests from the spec; implementer made them pass.
- Tests: 119 passed in 1.26s, lint clean: yes
- Problems / surprises: see the agents' summaries in the run log (logs/agent-runs.jsonl).
- Next: review the PR

## 2026-11-19 — F-003 Max loss rule (R2)
- Agent/model: agent pipeline: test-writer (claude-opus-5-5, 15 turns, $0.48, 39s); implementer (claude-opus-5-5, 15 turns, $0.24, 30s)
- Done: test-writer wrote failing tests from the spec; implementer made them pass.
- Tests: 115 passed in 1.42s, lint clean: yes
- Problems / surprises: see the agents' summaries in the run log (logs/agent-runs.jsonl).
- Next: review the PR

## 2026-11-11 — Harness: hosted check feedback (no feature)
- Agent/model: Claude Code (Opus 5.5), from the reviewer agent's findings on PRs #3 and #4
- Done: the hosted orchestrator's checks run in a copy without `evals/`, refuse
  symlinks, and send the agent only lint output and pass/fail (counts come from the
  junit report, for the log only). Mount path and budget unit verified against the
  Managed Agents docs and pinned by tests. Review rounds update progress.md and the
  PR body; Ctrl-C still logs the run.
- Tests: all pass, lint clean: yes
- Problems / surprises: agent code still runs on the host during the checks, so
  pass/fail is a ~1 bit per retry side channel until the checks run in a container.
- Next: `ant apply` the hosted agents, then a first hosted run on F-003.

## 2026-11-10 — F-002 Max daily loss rule
- Agent/model: Claude Code (Opus 5.5)
- Done: `check_max_daily_loss` per spec R1. Day-start balance carries over from the
  last closed balance before local midnight; floating P&L at day end is ignored;
  breach only when equity is strictly below the limit.
- Tests: 22/22, lint clean: yes (13 new tests covering every acceptance criterion,
  plus closed intraday losses, a non-Prague timezone, midnight exactly and a custom %)
- Problems / surprises: none. Not a clean eval sample: this agent also wrote the
  F-002 hidden tests in the scaffold session. `git push` for the PR was denied by
  `.claude/settings.json` as intended; a human pushes the branch.
- Next: F-003 max loss rule

## 2026-11-10 — F-001 Models and profit target rule
- Agent/model: scaffolded by hand with Claude Code
- Done: project layout, AGENTS.md/CLAUDE.md, spec, models, profit target rule,
  stubs for the other rules, first eval task (max daily loss) with hidden tests.
- Tests: all passing, lint clean: yes
- Next: F-002 max daily loss rule
