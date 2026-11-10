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

## 2026-11-10 — F-002 Max daily loss rule
- Agent/model: Claude Code (Opus 5.5)
- Done: `check_max_daily_loss` per spec R1. Day-start balance carries over from the
  last closed balance before local midnight; floating P&L at day end is ignored;
  breach only when equity is strictly below the limit.
- Tests: 22/22, lint clean: yes (13 new tests covering every acceptance criterion,
  plus closed intraday losses, a non-Prague timezone, midnight exactly and a custom %)
- Problems / surprises: none. Not a clean eval sample: this agent also wrote the
  F-002 hidden tests in the scaffold session.
- Next: F-003 max loss rule

## 2026-11-10 — F-001 Models and profit target rule
- Agent/model: scaffolded by hand with Claude Code
- Done: project layout, AGENTS.md/CLAUDE.md, spec, models, profit target rule,
  stubs for the other rules, first eval task (max daily loss) with hidden tests.
- Tests: all passing, lint clean: yes
- Next: F-002 max daily loss rule
