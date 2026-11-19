"""A local web dashboard for the agent pipeline: see every run, start or stop one.

Usage:
    .venv/bin/python -m observe                     # http://127.0.0.1:8765
    .venv/bin/python -m observe --port 9000 --log-dir ~/runs --no-github

Standard library only. It binds to 127.0.0.1, refuses requests whose Host isn't
local (DNS rebinding), and every request that changes something needs the
token baked into the page it served, so another site open in your browser
can't start a run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness.pipeline import load_features

from . import data

STATIC = Path(__file__).resolve().parent / "static"
MODULES = {"local": "harness.run_feature", "hosted": "harness.managed.run"}
GH_CACHE_SECONDS = 60


class StartError(Exception):
    """A run can't be started; the message is shown to the user."""


class RunManager:
    """Starts at most one pipeline run at a time and writes its output to a log file.

    One run at a time because the pipeline switches branches and commits in
    this checkout: two runs would trample each other's working tree.
    """

    def __init__(self, root: Path, python: str) -> None:
        self.root = root
        self.python = python
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.current: dict[str, Any] | None = None

    def status(self) -> dict | None:
        with self.lock:
            if self.current is None:
                return None
            return {**self.current, "running": self.process is not None}

    def validate(self, request: dict, features: list[dict]) -> list[str]:
        """The command line for a start request, or StartError explaining why not."""
        feature = str(request.get("feature", ""))
        mode = str(request.get("mode", "local"))
        if mode not in MODULES:
            raise StartError(f"Unknown mode {mode!r}.")
        known = {f["id"]: f for f in features}
        if not re.fullmatch(r"F-\d{3}", feature) or feature not in known:
            raise StartError(f"No feature {feature!r} in feature_list.json.")
        if known[feature].get("passes"):
            raise StartError(f"{feature} already passes.")
        try:
            budget = float(request.get("budget", 2))
        except (TypeError, ValueError):
            raise StartError("Budget must be a number.") from None
        if not 0 < budget <= 20:
            raise StartError("Budget must be more than $0 and at most $20 per agent.")
        cmd = [self.python, "-m", MODULES[mode], feature, "--budget", f"{budget:g}"]
        if not request.get("push"):
            cmd.append("--no-push")
        return cmd

    def start(self, request: dict, features: list[dict]) -> dict:
        cmd = self.validate(request, features)
        with self.lock:
            if self.process is not None:
                raise StartError(f"{self.current['feature']} is still running. Stop it first.")
            if data.git(self.root, "status", "--porcelain", "--untracked-files=all"):
                raise StartError(
                    "The working tree has uncommitted changes; the pipeline needs it clean."
                )
            log_dir = self.root / data.RUN_LOG_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            log_path = log_dir / f"{cmd[3]}-{stamp}.log"
            log = log_path.open("w")
            log.write(f"$ {' '.join(cmd[1:])}\n")
            log.flush()
            self.process = subprocess.Popen(
                cmd,
                cwd=self.root,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=True,  # so Stop can signal the whole run, not just python
            )
            self.current = {
                "feature": cmd[3],
                "mode": request.get("mode", "local"),
                "log": log_path.name,
                "pid": self.process.pid,
                "started": time.time(),
                "command": " ".join(cmd[1:]),
                "exit_code": None,
            }
            threading.Thread(target=self._wait, args=(self.process, log), daemon=True).start()
            return dict(self.current)

    def _wait(self, process: subprocess.Popen, log: Any) -> None:
        code = process.wait()
        log.write(f"EXIT {code}\n")
        log.close()
        with self.lock:
            if self.process is process:
                self.process = None
                if self.current:
                    self.current["exit_code"] = code
                    self.current["ended"] = time.time()

    def stop(self) -> dict:
        """Ctrl-C the run (the pipeline logs it and stops cleanly); kill it if that fails."""
        with self.lock:
            process = self.process
        if process is None:
            raise StartError("Nothing is running.")
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
        return {"stopping": True}


class Dashboard:
    def __init__(self, root: Path, log_dirs: list[Path], github: bool, projects_dir: Path) -> None:
        self.root = root
        self.log_dirs = [root / data.RUN_LOG_DIR, *log_dirs]
        self.github = github
        self.projects_dir = projects_dir
        self.token = secrets.token_urlsafe(24)
        python = root / ".venv" / "bin" / "python"
        self.runs = RunManager(root, str(python) if python.exists() else sys.executable)
        self._prs: tuple[float, list[dict] | None] = (0.0, None)

    def prs(self) -> list[dict] | None:
        if not self.github:
            return None
        at, cached = self._prs
        if time.time() - at > GH_CACHE_SECONDS:
            self._prs = (time.time(), data.pull_requests(self.root))
        return self._prs[1]

    def features(self) -> list[dict]:
        return load_features(self.root)["features"]

    def state(self) -> dict:
        features = self.features()
        branches = {}
        for feature in features:
            name = data.branch_name(feature)
            branches[name] = data.branch_commits(self.root, name)
        ledgers = [
            data.git(self.root, "show", f"{ref}:{data.RUN_LEDGER}") or ""
            for ref, _ in branches.values()
            if ref
        ]
        runs = data.load_runs(self.root, ledgers)
        prs = self.prs()
        main = data.main_ref(self.root)
        main_list = data.git(self.root, "show", f"{main}:feature_list.json")
        try:
            merged = {f["id"] for f in json.loads(main_list or "")["features"] if f.get("passes")}
        except (ValueError, KeyError, TypeError):
            merged = None
        board = data.feature_board(features, runs, branches, prs, merged)
        repo = data.github_repo(data.git(self.root, "remote", "get-url", "origin"))
        current = data.git(self.root, "branch", "--show-current")
        return {
            "repo": repo,
            "checkout": (current or "").strip(),
            "main": {
                "ref": main,
                "sha": (data.git(self.root, "rev-parse", "--short", main or "HEAD") or "").strip(),
            },
            "github": prs is not None,
            "overview": data.overview(runs, board),
            "board": board,
            "runs": runs,
            "denials": data.denial_summary(runs),
            "logs": data.list_logs(self.log_dirs),
            "active": self.runs.status(),
            "generated": time.time(),
        }


def make_handler(app: Dashboard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "agent-dashboard"

        def log_message(self, format: str, *args: Any) -> None:  # quiet; the page polls
            pass

        def _local_host(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def do_GET(self) -> None:
            if not self._local_host():
                return self._error(HTTPStatus.FORBIDDEN, "Open the dashboard via localhost.")
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/":
                page = (STATIC / "index.html").read_text().replace("__TOKEN__", app.token)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self._json(app.state())
            if url.path == "/api/log":
                path = data.safe_log_path(app.log_dirs, query.get("name", ""))
                if path is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No such log.")
                text, offset = data.read_from(path, int(query.get("offset", "0") or 0))
                return self._json({"text": text, "offset": offset})
            if url.path == "/api/transcript":
                path = data.find_transcript(query.get("session", ""), app.projects_dir)
                if path is None:
                    return self._error(
                        HTTPStatus.NOT_FOUND,
                        f"No transcript for this session under {app.projects_dir}. "
                        "It is only on the machine that ran the agent.",
                    )
                return self._json({"entries": data.parse_transcript(path.read_text())})
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")

        def do_POST(self) -> None:
            if not self._local_host():
                return self._error(HTTPStatus.FORBIDDEN, "Open the dashboard via localhost.")
            if not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                return self._error(HTTPStatus.FORBIDDEN, "Missing or wrong token; reload the page.")
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._error(HTTPStatus.BAD_REQUEST, "Body must be JSON.")
            try:
                if self.path == "/api/runs":
                    return self._json(app.runs.start(body, app.features()), 201)
                if self.path == "/api/runs/stop":
                    return self._json(app.runs.stop())
            except StartError as e:
                return self._error(HTTPStatus.CONFLICT, str(e))
            return self._error(HTTPStatus.NOT_FOUND, "Not found.")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--log-dir",
        action="append",
        default=[],
        type=Path,
        help="Another folder of run logs to show (repeatable). logs/runs/ is always shown.",
    )
    parser.add_argument("--no-github", dest="github", action="store_false", help="Don't call gh")
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Where Claude Code keeps session transcripts",
    )
    args = parser.parse_args(argv)
    app = Dashboard(
        data.ROOT, [d.expanduser() for d in args.log_dir], args.github, args.projects_dir
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    print(f"Agent dashboard on http://127.0.0.1:{args.port}  (Ctrl-C to quit)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
