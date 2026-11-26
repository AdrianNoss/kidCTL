"""Minimaler GitHub-REST-Client (nur Standardbibliothek). Braucht ein Fine-grained-Token, das nur für das eine
Repo gilt und nur *Issues: Read and write* (plus das automatische *Metadata: Read*) hat."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

API = "https://api.github.com"
LABELS = ["feature-request", "needs-triage"]  # fest im Code, nie aus Modell- oder Mailinhalt


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str, opener=urllib.request.urlopen):
        if repo.count("/") != 1:
            raise ValueError("FEATURE_INBOX_GITHUB_REPO muss die Form owner/name haben")
        self.repo = repo
        self.token = token
        self._open = opener

    def _req(self, method: str, path: str, query: dict | None = None, body: dict | None = None):
        url = f"{API}/repos/{self.repo}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "kidctl-feature-inbox")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout=30) as resp:
                return json.loads(resp.read() or b"null")
        except OSError as exc:  # HTTPError/URLError sind OSError; Token nie mitloggen
            raise GitHubError(f"GitHub {method} {path}: {type(exc).__name__} {getattr(exc, 'code', '')}") from None

    def feature_issues(self, state: str = "all", pages: int = 5) -> list[dict]:
        """Issues mit Label ``feature-request`` (ohne Pull Requests)."""
        out: list[dict] = []
        for page in range(1, pages + 1):
            batch = self._req("GET", "/issues", {"labels": "feature-request", "state": state, "per_page": 100,
                                                 "page": page})
            if not isinstance(batch, list):
                break
            out += [i for i in batch if isinstance(i, dict) and "pull_request" not in i]
            if len(batch) < 100:
                break
        return out

    def create_issue(self, title: str, body: str) -> int:
        res = self._req("POST", "/issues", body={"title": title, "body": body, "labels": list(LABELS)})
        if not isinstance(res, dict) or not isinstance(res.get("number"), int):
            raise GitHubError("GitHub: unerwartete Antwort beim Anlegen")
        return res["number"]
