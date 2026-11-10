"""Turn the reviewer agent's verdict into a GitHub review.

The agent only suggests a verdict. This script decides what is actually
submitted, so the rules live in code rather than in a prompt:
- Harness files changed (needs_human) -> never approve; comment instead.
- Missing or malformed verdict -> comment and fail the job.

Env: REVIEW_JSON, NEEDS_HUMAN, PR_NUMBER, GH_TOKEN, MODEL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

VERDICTS = {"approve", "request_changes"}


def decide(review: dict | None, needs_human: bool, model: str = "Claude") -> tuple[str, str]:
    """Return (gh review flag, body)."""
    if not review or review.get("verdict") not in VERDICTS or not review.get("summary"):
        return "--comment", (
            "**Automated review failed**: the reviewer agent returned no valid verdict. "
            "A human needs to review this PR."
        )

    lines = [f"**Automated review** ({model}). CI checks passed.", "", review["summary"]]
    blocking = review.get("blocking_issues") or []
    suggestions = review.get("suggestions") or []
    if blocking:
        lines += ["", "**Blocking issues**", *[f"- {b}" for b in blocking]]
    if suggestions:
        lines += ["", "**Suggestions (non-blocking)**", *[f"- {s}" for s in suggestions]]

    verdict = review["verdict"]
    if verdict == "approve" and blocking:
        verdict = "request_changes"  # an approval with blocking issues is contradictory

    if verdict == "approve" and needs_human:
        lines += [
            "",
            "This PR changes harness files (evals, spec, agent instructions or CI), "
            "so the reviewer agent may not approve it. **A human must approve.**",
        ]
        return "--comment", "\n".join(lines)

    return ("--approve" if verdict == "approve" else "--request-changes"), "\n".join(lines)


def main() -> int:
    raw = os.environ.get("REVIEW_JSON", "")
    try:
        review = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        review = None
    needs_human = os.environ.get("NEEDS_HUMAN", "false").lower() == "true"
    flag, body = decide(review, needs_human, os.environ.get("MODEL", "Claude"))

    pr = os.environ["PR_NUMBER"]
    print(f"Submitting review {flag} on PR #{pr}")
    result = subprocess.run(["gh", "pr", "review", pr, flag, "--body", body], text=True)
    if result.returncode != 0 and flag in ("--approve", "--request-changes"):
        print(
            "::error::Could not submit the review. Enable 'Allow GitHub Actions to create "
            "and approve pull requests' in Settings > Actions > General.",
            file=sys.stderr,
        )
        return result.returncode
    return 1 if review is None else result.returncode


if __name__ == "__main__":
    sys.exit(main())
