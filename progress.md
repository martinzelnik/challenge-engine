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

## 2026-11-19 — Harness: review-responder for PR comments (no feature)
- Agent/model: Claude Code (Opus 5.5)
- Done: new review-responder role (`.claude/agents/review-responder.md`, policy
  `REVIEW_RESPONDER`: src/ and tests/, pytest and ruff) and `harness/address_comments.py`.
  It answers trusted review comments, checks the fix in code (paths, no removed tests or
  assertions, green checks), commits, pushes feat/* and replies to each comment. Chained
  from `run_feature --review-rounds N`, and in CI by `address-comments.yml` (opt-in).
  Also fixed the hosted review loop, which skipped the reviewer's inline comments
  (they come from claude[bot], not github-actions[bot]).
- Tests: all pass, lint clean: yes. Dry-run against a scratch clone with a stand-in
  agent: a valid fix was committed; a fix that removed assertions was refused and discarded.
- Problems / surprises: as in the other pipelines, agent-written code runs during the
  checks with ANTHROPIC_API_KEY in the environment (roadmap item 4).
- Next: set AUTO_ADDRESS_COMMENTS and AGENT_PUSH_TOKEN, then watch a real PR.

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
