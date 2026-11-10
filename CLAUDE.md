@AGENTS.md

## Claude Code specifics

- `.claude/settings.json` blocks reading `.env` files and the eval hidden tests.
  If a tool call is denied, don't look for a way around it: note it in
  `progress.md` and carry on.
- For long sessions, keep `progress.md` current as you go, so a fresh session
  (or one after compaction) can pick up from it without the chat history.
- Use subagents for broad searches, not for writing code. One writer per session.
