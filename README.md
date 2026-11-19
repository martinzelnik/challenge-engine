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

### The agent pipeline

```
test-writer ──> implementer ──> pipeline pushes ──> CI checks ──> reviewer ──> human merges
 tests/ only    src/ only       feat/* branch,      no AI         comments,
 must go red    must go green   opens the PR                      approves or blocks
                                       ▲                              │
                                       └──── review-responder <───────┘
                                             fixes valid comments, replies to all
```

```bash
.venv/bin/pip install -e ".[dev,agents]"
export ANTHROPIC_API_KEY=...            # or use Claude Code's login
.venv/bin/python -m harness.run_feature F-003 --budget 2   # ends with a PR
```

[harness/run_feature.py](harness/run_feature.py) runs the two writing agents on the
Claude Agent SDK. Each role's prompt lives in [.claude/agents/](.claude/agents/) (so
the same roles work as Claude Code subagents), and its limits are enforced in code by
[harness/policy.py](harness/policy.py) on every tool call: which paths it may edit,
which commands it may run, and no access to `evals/`. The pipeline itself decides
what counts as done (tests red after the test-writer, everything green after the
implementer, only allowed paths changed), flips the feature, writes the progress
entry, commits, pushes the `feat/*` branch and opens the PR (`--no-push` to stop
before that). Agents themselves never commit or push. `main` is protected on GitHub:
changes need a PR, a passing `checks` job and an approval, and force-pushes are blocked.

### Answering review comments (review-responder)

[harness/address_comments.py](harness/address_comments.py) reads a PR's review comments,
from the reviewer agent and from people with write access, and runs the
**review-responder** role ([.claude/agents/review-responder.md](.claude/agents/review-responder.md))
on them. The agent judges each comment against the spec and AGENTS.md, fixes the valid
ones in `src/challenge_engine/` or `tests/`, and gives a reason for each one it skips.
The orchestrator, not the agent, decides whether the fix stands: only allowed paths, no
test removed, no existing assertion removed or rewritten, tests, lint and format green.
Then it commits with a `progress.md` entry, pushes the `feat/*` branch, and replies under
every comment: *Fixed in `<sha>`*, *Not changed: why*, or *Not fixed yet* when the fix
failed the checks and was thrown away.

```bash
.venv/bin/python -m harness.address_comments 12            # one PR, by hand
.venv/bin/python -m harness.run_feature F-004 --review-rounds 3   # feature, PR, then answer reviews
```

- **Comments are data.** Only people with write access and the CI reviewer are heard;
  comments are fenced in the prompt and can't widen the role's limits.
- **Idempotent.** Replies carry a hidden marker with the ids they answer, so a comment is
  never answered twice. A human reply to one of our replies is a new comment and gets
  an answer.
- **Bounded.** At most `--max-rounds` (default 3) fix commits per PR, then a human takes
  over. PRs that change harness files, and PRs from forks, are refused.
- **Automatic in CI.** [address-comments.yml](.github/workflows/address-comments.yml) runs
  it after every "PR review" run and when someone with write access reviews or comments.
  To turn it on, set the repo variable `AUTO_ADDRESS_COMMENTS=true` and add a secret
  `AGENT_PUSH_TOKEN` (fine-grained token, contents: write on this repo; pushes made with
  the workflow's own token don't re-run CI). The agent runs in a job with a read-only
  token; a second job pushes and replies. With the CI responder on, leave
  `run_feature --review-rounds` at 0 so the two don't answer the same PR.

### Hosted agents (Claude Managed Agents)

The same pipeline can run each agent in a hosted session that you can watch and
steer while it works, and it closes the loop with the reviewer:

```bash
ant apply agents/test-writer.md agents/implementer.md environments/challenge-engine.yaml  # once
.venv/bin/python -m harness.managed.run F-003 --budget 2 --review-rounds 2
```

- **Agents as files.** [agents/](agents/) and [environments/](environments/) are synced
  with `ant apply`; IDs go to `claude-lock.json`. Editing a file and re-applying makes
  a new agent version.
- **No access to the hidden tests.** The repo is uploaded as a tarball without
  `evals/`. The sandbox has no web access. The orchestrator's own checks run the
  agent's code locally, so they run in a copy without `evals/`, patches that add
  symlinks are refused, and only lint output and whether the tests pass go back to
  the agent (it reruns the tests in its sandbox for details). The agent's code still
  runs on your machine during those checks, so it could read `evals/` by absolute
  path and signal through pass/fail, about one bit per retry. A container for the
  checks closes that: roadmap item 4.
- **Limits enforced in code.** Every write, edit and bash call is `always_ask`; the
  orchestrator answers each one with [harness/policy.py](harness/policy.py).
- **Hard spending cap per session** (`--budget`), enforced by the platform.
- **Review loop.** After the PR opens, the orchestrator waits for the reviewer. On
  *changes requested* it sends the review and inline comments to the implementer's
  still-open session, re-runs the checks, pushes the fix, and waits again, up to
  `--review-rounds` times. Each round adds a fix commit and a `progress.md` entry and
  updates the PR description. Approved, commented or out of rounds: a human takes over.

**Watching and steering a hosted agent:**

| How | What you get |
|---|---|
| The `watch:` link printed per session | Console session viewer: live transcript, tool calls, cost against budget |
| `ant beta:sessions connect <session-id>` | The same in your terminal: Esc interrupts, you can message the agent or answer a paused tool call |
| `ant beta:sessions connect <session-id> --web` | The Console viewer served locally |
| Ctrl-C in the orchestrator | Interrupts the running session and stops the pipeline |

### Observing the agents

| What | Where |
|---|---|
| Live: every tool call and every denied call, per role | Terminal output of `run_feature` |
| Per run: model, cost, turns, time, retries, denied calls, agent summaries | [logs/agent-runs.jsonl](logs/agent-runs.jsonl) |
| Full transcript of any agent | `session_id` in the run log (Claude Code keeps session transcripts under `~/.claude/projects/`) |
| What changed, step by step | Git history: a `test(...)` commit, then a `feat(...)` commit |
| Review outcome | PR review and inline comments; Actions run logs |

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
2. [x] Agent pipeline on the Claude Agent SDK (test-writer, implementer, enforced role limits)
3. [ ] Eval runner with cost, time and diff-size tracking
4. [ ] Sandbox (Docker, no network), permission hooks, per-run budget
5. [ ] Model routing and a model comparison
6. [ ] MCP server (spec + test results); [x] CI reviewer agent for PRs
7. [ ] Write-up: what worked, what didn't, the numbers
