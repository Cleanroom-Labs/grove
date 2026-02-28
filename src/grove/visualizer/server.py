"""
HTTP server for the web-based visualizer.

Serves static web assets and provides a JSON API for repository data
and git operations. Uses only stdlib (http.server, threading, json).
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from .data import (
    compare_worktrees,
    load_and_validate_repos,
    repos_to_json,
    worktrees_to_json,
)

# Content types for static files
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}

# Allowed static file names (prevents directory traversal)
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/style.css": "style.css",
    "/app.js": "app.js",
    "/graph.js": "graph.js",
    "/layout.js": "layout.js",
    "/worktree.js": "worktree.js",
    "/actions.js": "actions.js",
}


class VisualizerState:
    """Shared mutable state for the visualizer server."""

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.repos = []
        self.lock = threading.Lock()
        self.reload()

    def reload(self):
        """Reload repository data (thread-safe)."""
        with self.lock:
            self.repos = load_and_validate_repos(self.repo_path)

    def get_repos_json(self) -> dict:
        with self.lock:
            return repos_to_json(self.repos)

    def get_worktrees_json(self) -> dict:
        with self.lock:
            return worktrees_to_json(self.repo_path)

    def find_repo(self, path_str: str):
        """Find a RepoInfo by its path string."""
        with self.lock:
            for repo in self.repos:
                if str(repo.path) == path_str:
                    return repo
        return None


def make_handler_class(state: VisualizerState):
    """Create a handler class bound to the given state."""

    class VisualizerHandler(BaseHTTPRequestHandler):
        """HTTP request handler for the visualizer."""

        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            # Static files
            if path in STATIC_FILES:
                self._serve_static(STATIC_FILES[path])
                return

            # API endpoints
            if path == "/api/repos":
                self._json_response(state.get_repos_json())
                return

            if path == "/api/worktrees":
                self._json_response(state.get_worktrees_json())
                return

            if path == "/api/worktree":
                params = parse_qs(parsed.query)
                wt_path = params.get("path", [None])[0]
                if wt_path:
                    try:
                        repos = load_and_validate_repos(Path(wt_path))
                        self._json_response(repos_to_json(repos))
                    except Exception as e:
                        self._json_response({"ok": False, "error": str(e)}, status=500)
                else:
                    self._json_response(
                        {"ok": False, "error": "Missing path parameter"}, status=400
                    )
                return

            if path == "/api/compare":
                params = parse_qs(parsed.query)
                base = params.get("base", [None])[0]
                other = params.get("other", [None])[0]
                if base and other:
                    try:
                        result = compare_worktrees(Path(base), Path(other))
                        self._json_response(result)
                    except Exception as e:
                        self._json_response({"ok": False, "error": str(e)}, status=500)
                else:
                    self._json_response(
                        {"ok": False, "error": "Missing base or other parameter"},
                        status=400,
                    )
                return

            self._not_found()

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path

            body = self._read_json_body()

            if path == "/api/action/refresh":
                state.reload()
                self._json_response({"ok": True})
                return

            if path == "/api/action/fetch":
                repo_path = body.get("path", "")
                repo = state.find_repo(repo_path)
                if not repo:
                    self._json_response(
                        {"ok": False, "error": f"Repository not found: {repo_path}"},
                        status=404,
                    )
                    return
                success = repo.fetch()
                if success:
                    repo.validate(
                        check_sync=True, allow_detached=True, allow_no_remote=True
                    )
                self._json_response(
                    {
                        "ok": success,
                        "error": "" if success else f"Fetch failed for {repo.name}",
                    }
                )
                return

            if path == "/api/action/fetch-all":
                failed = []
                with state.lock:
                    for repo in state.repos:
                        if not repo.fetch():
                            failed.append(repo.name)
                    for repo in state.repos:
                        repo.validate(
                            check_sync=True, allow_detached=True, allow_no_remote=True
                        )
                self._json_response(
                    {
                        "ok": len(failed) == 0,
                        "error": f"Failed to fetch: {', '.join(failed)}"
                        if failed
                        else "",
                    }
                )
                return

            if path == "/api/action/push":
                repo_path = body.get("path", "")
                repo = state.find_repo(repo_path)
                if not repo:
                    self._json_response(
                        {"ok": False, "error": f"Repository not found: {repo_path}"},
                        status=404,
                    )
                    return
                if repo.ahead_count == "0":
                    self._json_response({"ok": True, "error": "Nothing to push"})
                    return
                success = repo.push()
                if success:
                    repo.validate(
                        check_sync=True, allow_detached=True, allow_no_remote=True
                    )
                self._json_response(
                    {
                        "ok": success,
                        "error": "" if success else f"Push failed for {repo.name}",
                    }
                )
                return

            if path == "/api/action/push-all":
                from grove.repo_utils import topological_sort_repos

                with state.lock:
                    to_push = [
                        r for r in state.repos if r.ahead_count not in ("0", None)
                    ]
                    if not to_push:
                        self._json_response({"ok": True, "error": "Nothing to push"})
                        return

                    sorted_repos = topological_sort_repos(to_push)
                    failed = []
                    for repo in sorted_repos:
                        if not repo.push():
                            failed.append(repo.name)

                    for repo in state.repos:
                        repo.validate(
                            check_sync=True, allow_detached=True, allow_no_remote=True
                        )

                self._json_response(
                    {
                        "ok": len(failed) == 0,
                        "error": f"Failed to push: {', '.join(failed)}"
                        if failed
                        else "",
                    }
                )
                return

            if path == "/api/action/checkout":
                repo_path = body.get("path", "")
                branch = body.get("branch", "")
                repo = state.find_repo(repo_path)
                if not repo:
                    self._json_response(
                        {"ok": False, "error": f"Repository not found: {repo_path}"},
                        status=404,
                    )
                    return
                if not branch:
                    self._json_response(
                        {"ok": False, "error": "Missing branch"}, status=400
                    )
                    return
                success, error = repo.checkout(branch)
                if success:
                    repo.validate(
                        check_sync=True, allow_detached=True, allow_no_remote=True
                    )
                self._json_response({"ok": success, "error": error})
                return

            self._not_found()

        def _serve_static(self, filename: str):
            """Serve a static file from the web package."""
            suffix = Path(filename).suffix
            content_type = CONTENT_TYPES.get(suffix, "application/octet-stream")

            try:
                web_pkg = resources.files("grove.visualizer.web")
                content = (web_pkg / filename).read_text()
            except (FileNotFoundError, TypeError):
                self._not_found()
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        def _json_response(self, data: dict, status: int = 200):
            """Send a JSON response."""
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            """Read and parse JSON from the request body."""
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        def _not_found(self):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    return VisualizerHandler


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a separate thread."""

    daemon_threads = True
    allow_reuse_address = True


def find_free_port() -> int:
    """Find a free port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_server(repo_path: Path, open_browser: bool = True) -> int:
    """Start the visualizer server and block until interrupted.

    Args:
        repo_path: Path to the git repository root.
        open_browser: Whether to open the browser automatically.

    Returns:
        Exit code (0 for success).
    """
    import webbrowser

    state = VisualizerState(repo_path)
    port = find_free_port()
    handler_class = make_handler_class(state)
    server = ThreadedHTTPServer(("127.0.0.1", port), handler_class)

    url = f"http://127.0.0.1:{port}"
    print(f"Grove visualizer running at {url}")
    print("Press Ctrl+C to stop")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()

    return 0
