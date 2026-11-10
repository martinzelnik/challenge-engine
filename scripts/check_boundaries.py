"""Check a PR against the agent boundaries in AGENTS.md.

Usage:
    python scripts/check_boundaries.py --base <base-commit>

Fails (exit 1) when the PR breaks a rule an agent must never break:
- changes a field in feature_list.json other than "passes"
- adds or removes features
- marks more than one feature as passing, or un-marks one

Doesn't fail, but sets needs_human=true, when the PR touches harness files
(evals, spec, agent instructions, CI). Those changes are allowed, but only a
human may approve them.

Writes `flipped` and `needs_human` to $GITHUB_OUTPUT when run in GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

PROTECTED_PREFIXES = (
    "evals/",
    "docs/spec.md",
    ".claude/",
    ".github/",
    "scripts/",
    "harness/",
    "agents/",
    "environments/",
    "claude-lock.json",
    "AGENTS.md",
    "CLAUDE.md",
    "REVIEW.md",
)


@dataclass
class Report:
    violations: list[str] = field(default_factory=list)
    protected_changes: list[str] = field(default_factory=list)
    flipped: list[str] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return bool(self.protected_changes)


def check_feature_list(before: dict, after: dict, report: Report) -> None:
    old = {f["id"]: f for f in before.get("features", [])}
    new = {f["id"]: f for f in after.get("features", [])}

    if old.keys() != new.keys():
        added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
        report.violations.append(f"feature_list.json: features added {added} / removed {removed}")

    def top_level(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "features"}

    if top_level(before) != top_level(after):
        report.violations.append("feature_list.json: top-level fields changed")

    for fid in sorted(old.keys() & new.keys()):
        o, n = old[fid], new[fid]
        changed = sorted(k for k in o.keys() | n.keys() if k != "passes" and o.get(k) != n.get(k))
        if changed:
            report.violations.append(f"feature_list.json: {fid} changed {changed}")
        if o.get("passes") and not n.get("passes"):
            report.violations.append(f"feature_list.json: {fid} went from passing to failing")
        if not o.get("passes") and n.get("passes"):
            report.flipped.append(fid)

    if len(report.flipped) > 1:
        report.violations.append(
            f"feature_list.json: {len(report.flipped)} features marked passing "
            f"({', '.join(report.flipped)}); one feature per PR"
        )


def check_paths(changed_paths: list[str], report: Report) -> None:
    report.protected_changes = sorted(
        p for p in changed_paths if any(p == pre or p.startswith(pre) for pre in PROTECTED_PREFIXES)
    )


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _load_feature_list(ref: str | None) -> dict:
    if ref is None:
        with open("feature_list.json") as f:
            return json.load(f)
    try:
        return json.loads(_git("show", f"{ref}:feature_list.json"))
    except subprocess.CalledProcessError:
        return {"features": []}


def build_report(base: str) -> Report:
    report = Report()
    changed = [p for p in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if p]
    check_paths(changed, report)
    if "feature_list.json" in changed:
        check_feature_list(_load_feature_list(base), _load_feature_list("HEAD"), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="Base commit of the PR")
    args = parser.parse_args()

    report = build_report(args.base)
    print(
        json.dumps(
            {
                "violations": report.violations,
                "protected_changes": report.protected_changes,
                "flipped": report.flipped,
                "needs_human": report.needs_human,
            },
            indent=2,
        )
    )

    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as f:
            f.write(f"flipped={' '.join(report.flipped)}\n")
            f.write(f"needs_human={str(report.needs_human).lower()}\n")

    for v in report.violations:
        print(f"::error::{v}", file=sys.stderr)
    return 1 if report.violations else 0


if __name__ == "__main__":
    sys.exit(main())
