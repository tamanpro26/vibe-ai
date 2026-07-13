"""
tools/github_tool.py
Deep GitHub integration for VibeAI agents.

Actions (call via execute(action, **params)):
  repo_info       — full repository metadata
  repo_create     — create a new repository
  branch_list     — list branches
  branch_create   — create a branch from ref
  pr_create       — open a pull request
  pr_list         — list pull requests
  pr_merge        — merge a pull request
  pr_review       — submit a PR review (approve / request_changes / comment)
  pr_diff         — get the diff of a PR
  pr_comments     — get all review comments on a PR
  issue_create    — open an issue
  issue_list      — list issues
  issue_close     — close an issue
  issue_comment   — add a comment to an issue
  file_read       — read a file from any branch
  file_write      — create or update a file on a branch
  search_code     — search code across GitHub
  search_repos    — search repositories
  actions_list    — list workflow runs
  actions_run     — trigger a workflow dispatch
  release_create  — create a release tag
  release_list    — list releases
  commit_list     — list recent commits on a branch
  commit_diff     — get diff for a specific commit SHA

All calls require GITHUB_TOKEN in .env.
Depth is controlled by callers: pass explicit owner/repo for single-repo
ops, or leave out to operate on the authenticated user's own repos.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp
from loguru import logger

try:
    from config.settings import settings
    _TOKEN = settings.github_token
except Exception:
    _TOKEN = ""

_API = "https://api.github.com"
_HEADERS = {
    "Accept":               "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _auth_headers(token: str = "") -> dict:
    t = (token or _TOKEN).strip()
    if not t:
        return _HEADERS
    return {**_HEADERS, "Authorization": f"Bearer {t}"}


class GitHubTool:
    """
    Stateless GitHub REST API wrapper.
    All methods are coroutines (async); call via execute().
    """

    def __init__(self, token: str = "") -> None:
        self._token = token or _TOKEN

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> Any:
        async with aiohttp.ClientSession(headers=_auth_headers(self._token)) as s:
            async with s.get(f"{_API}{path}", params=params) as r:
                return await self._parse(r)

    async def _post(self, path: str, body: dict) -> Any:
        async with aiohttp.ClientSession(headers=_auth_headers(self._token)) as s:
            async with s.post(f"{_API}{path}", json=body) as r:
                return await self._parse(r)

    async def _put(self, path: str, body: dict) -> Any:
        async with aiohttp.ClientSession(headers=_auth_headers(self._token)) as s:
            async with s.put(f"{_API}{path}", json=body) as r:
                return await self._parse(r)

    async def _patch(self, path: str, body: dict) -> Any:
        async with aiohttp.ClientSession(headers=_auth_headers(self._token)) as s:
            async with s.patch(f"{_API}{path}", json=body) as r:
                return await self._parse(r)

    async def _delete(self, path: str) -> Any:
        async with aiohttp.ClientSession(headers=_auth_headers(self._token)) as s:
            async with s.delete(f"{_API}{path}") as r:
                return await self._parse(r)

    async def _get_raw(self, path: str) -> str:
        hdrs = {**_auth_headers(self._token), "Accept": "application/vnd.github.v3.diff"}
        async with aiohttp.ClientSession(headers=hdrs) as s:
            async with s.get(f"{_API}{path}") as r:
                if r.status >= 400:
                    text = await r.text()
                    return f"ERROR {r.status}: {text[:300]}"
                return await r.text()

    @staticmethod
    async def _parse(r: aiohttp.ClientResponse) -> Any:
        if r.status == 204:
            return {"status": "ok"}
        ct = r.headers.get("content-type", "")
        text = await r.text()
        if "json" in ct:
            try:
                return json.loads(text)
            except Exception:
                return {"raw": text}
        return {"raw": text}

    @staticmethod
    def _err(data: Any) -> str | None:
        if isinstance(data, dict) and "message" in data:
            return data["message"]
        return None

    # ── Actions ───────────────────────────────────────────────────────────────

    async def repo_info(self, owner: str, repo: str) -> str:
        data = await self._get(f"/repos/{owner}/{repo}")
        if e := self._err(data):
            return f"ERROR: {e}"
        return (
            f"Repo: {data['full_name']}\n"
            f"Description: {data.get('description') or '(none)'}\n"
            f"Stars: {data.get('stargazers_count', 0)} | Forks: {data.get('forks_count', 0)}\n"
            f"Default branch: {data.get('default_branch', 'main')}\n"
            f"Language: {data.get('language') or 'N/A'}\n"
            f"Open issues: {data.get('open_issues_count', 0)}\n"
            f"URL: {data.get('html_url', '')}\n"
            f"Clone URL: {data.get('clone_url', '')}"
        )

    async def repo_create(
        self,
        name:        str,
        description: str = "",
        private:     bool = False,
        auto_init:   bool = True,
    ) -> str:
        data = await self._post("/user/repos", {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": auto_init,
        })
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Created repo: {data.get('html_url', '')}"

    async def branch_list(self, owner: str, repo: str) -> str:
        data = await self._get(f"/repos/{owner}/{repo}/branches")
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        lines = [f"  {b['name']}" for b in data]
        return f"Branches in {owner}/{repo}:\n" + "\n".join(lines)

    async def branch_create(
        self,
        owner:    str,
        repo:     str,
        branch:   str,
        from_ref: str = "main",
    ) -> str:
        ref_data = await self._get(f"/repos/{owner}/{repo}/git/refs/heads/{from_ref}")
        if isinstance(ref_data, dict) and self._err(ref_data):
            return f"ERROR resolving {from_ref}: {self._err(ref_data)}"
        sha = ref_data.get("object", {}).get("sha", "")
        if not sha:
            return f"ERROR: Could not resolve SHA for {from_ref}"
        data = await self._post(f"/repos/{owner}/{repo}/git/refs", {
            "ref": f"refs/heads/{branch}",
            "sha": sha,
        })
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Created branch '{branch}' from '{from_ref}' ({sha[:8]})"

    async def pr_create(
        self,
        owner: str,
        repo:  str,
        title: str,
        head:  str,
        base:  str  = "main",
        body:  str  = "",
        draft: bool = False,
    ) -> str:
        data = await self._post(f"/repos/{owner}/{repo}/pulls", {
            "title": title,
            "head":  head,
            "base":  base,
            "body":  body,
            "draft": draft,
        })
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ PR #{data['number']} opened: {data.get('html_url', '')}"

    async def pr_list(
        self,
        owner: str,
        repo:  str,
        state: str = "open",
        limit: int = 20,
    ) -> str:
        data = await self._get(
            f"/repos/{owner}/{repo}/pulls",
            {"state": state, "per_page": min(limit, 100)},
        )
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        if not data:
            return f"No {state} pull requests in {owner}/{repo}."
        lines = [f"Pull Requests ({state}) in {owner}/{repo}:"]
        for pr in data:
            lines.append(
                f"  #{pr['number']} [{pr['state']}] {pr['title']} "
                f"({pr['head']['ref']} → {pr['base']['ref']})"
            )
        return "\n".join(lines)

    async def pr_merge(
        self,
        owner:          str,
        repo:           str,
        pr_number:      int,
        commit_message: str  = "",
        merge_method:   str  = "merge",
    ) -> str:
        body: dict[str, Any] = {"merge_method": merge_method}
        if commit_message:
            body["commit_message"] = commit_message
        data = await self._put(f"/repos/{owner}/{repo}/pulls/{pr_number}/merge", body)
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ PR #{pr_number} merged: {data.get('sha', '')}"

    async def pr_review(
        self,
        owner:     str,
        repo:      str,
        pr_number: int,
        event:     str,  # APPROVE | REQUEST_CHANGES | COMMENT
        body:      str  = "",
    ) -> str:
        event = event.upper()
        if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            return "ERROR: event must be APPROVE, REQUEST_CHANGES, or COMMENT"
        data = await self._post(f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews", {
            "event": event,
            "body":  body,
        })
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Review submitted on PR #{pr_number}: {event}"

    async def pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        diff = await self._get_raw(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        if diff.startswith("ERROR"):
            return diff
        return diff[:16_000] + ("\n... (diff truncated)" if len(diff) > 16_000 else "")

    async def pr_comments(self, owner: str, repo: str, pr_number: int) -> str:
        data = await self._get(f"/repos/{owner}/{repo}/pulls/{pr_number}/comments")
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        if not data:
            return f"No review comments on PR #{pr_number}."
        lines = [f"Review comments on PR #{pr_number}:"]
        for c in data:
            lines.append(f"  [{c['user']['login']}] {c.get('path','')}:{c.get('line','?')} — {c['body'][:120]}")
        return "\n".join(lines)

    async def issue_create(
        self,
        owner:    str,
        repo:     str,
        title:    str,
        body:     str   = "",
        labels:   list  = None,
        assignees: list = None,
    ) -> str:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        data = await self._post(f"/repos/{owner}/{repo}/issues", payload)
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Issue #{data['number']} created: {data.get('html_url', '')}"

    async def issue_list(
        self,
        owner: str,
        repo:  str,
        state: str = "open",
        limit: int = 20,
    ) -> str:
        data = await self._get(
            f"/repos/{owner}/{repo}/issues",
            {"state": state, "per_page": min(limit, 100)},
        )
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        # Filter out PRs (GitHub includes them in /issues)
        issues = [x for x in data if "pull_request" not in x]
        if not issues:
            return f"No {state} issues in {owner}/{repo}."
        lines = [f"Issues ({state}) in {owner}/{repo}:"]
        for i in issues:
            lines.append(f"  #{i['number']} [{i['state']}] {i['title']}")
        return "\n".join(lines)

    async def issue_close(self, owner: str, repo: str, issue_number: int) -> str:
        data = await self._patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            {"state": "closed"},
        )
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Issue #{issue_number} closed"

    async def issue_comment(
        self,
        owner:        str,
        repo:         str,
        issue_number: int,
        body:         str,
    ) -> str:
        data = await self._post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            {"body": body},
        )
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Comment added to issue #{issue_number}: {data.get('html_url', '')}"

    async def file_read(
        self,
        owner:  str,
        repo:   str,
        path:   str,
        ref:    str = "",
    ) -> str:
        params = {"ref": ref} if ref else {}
        data   = await self._get(f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", params)
        if e := self._err(data):
            return f"ERROR: {e}"
        if data.get("type") != "file":
            return f"ERROR: {path} is a directory, not a file"
        raw      = base64.b64decode(data["content"].replace("\n", "")).decode("utf-8", errors="replace")
        truncated = len(raw) > 16_000
        return raw[:16_000] + ("\n... (file truncated at 16 KB)" if truncated else "")

    async def file_write(
        self,
        owner:          str,
        repo:           str,
        path:           str,
        content:        str,
        message:        str  = "",
        branch:         str  = "",
    ) -> str:
        clean_path = path.lstrip("/")
        params: dict[str, Any] = {}
        if branch:
            params["ref"] = branch
        existing = await self._get(f"/repos/{owner}/{repo}/contents/{clean_path}", params)
        sha = existing.get("sha") if isinstance(existing, dict) and "sha" in existing else None

        encoded = base64.b64encode(content.encode("utf-8")).decode()
        payload: dict[str, Any] = {
            "message": message or f"{'Update' if sha else 'Create'} {clean_path} via VibeAI",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha
        if branch:
            payload["branch"] = branch

        data = await self._put(f"/repos/{owner}/{repo}/contents/{clean_path}", payload)
        if e := self._err(data):
            return f"ERROR: {e}"
        action  = "Updated" if sha else "Created"
        html    = data.get("content", {}).get("html_url", "")
        return f"✓ {action} {path}: {html}"

    async def search_code(
        self,
        query:  str,
        owner:  str = "",
        repo:   str = "",
        limit:  int = 10,
    ) -> str:
        q = query
        if owner and repo:
            q += f" repo:{owner}/{repo}"
        elif owner:
            q += f" user:{owner}"
        data = await self._get("/search/code", {"q": q, "per_page": min(limit, 30)})
        if e := self._err(data):
            return f"ERROR: {e}"
        items = data.get("items", [])
        if not items:
            return f"No code results for: {query}"
        lines = [f"Code search results for '{query}':"]
        for item in items:
            lines.append(f"  {item['repository']['full_name']} → {item['path']}")
            lines.append(f"    {item.get('html_url', '')}")
        return "\n".join(lines)

    async def search_repos(self, query: str, limit: int = 10) -> str:
        data = await self._get("/search/repositories", {"q": query, "per_page": min(limit, 30)})
        if e := self._err(data):
            return f"ERROR: {e}"
        items = data.get("items", [])
        if not items:
            return f"No repositories found for: {query}"
        lines = [f"Repository search for '{query}':"]
        for item in items:
            lines.append(
                f"  {item['full_name']} ⭐{item.get('stargazers_count', 0)} — "
                f"{(item.get('description') or '').strip()[:80]}"
            )
        return "\n".join(lines)

    async def actions_list(
        self,
        owner: str,
        repo:  str,
        limit: int = 10,
    ) -> str:
        data = await self._get(
            f"/repos/{owner}/{repo}/actions/runs",
            {"per_page": min(limit, 30)},
        )
        if e := self._err(data):
            return f"ERROR: {e}"
        runs = data.get("workflow_runs", [])
        if not runs:
            return f"No workflow runs in {owner}/{repo}."
        lines = [f"Workflow runs in {owner}/{repo}:"]
        for r in runs:
            lines.append(
                f"  #{r['id']} [{r['status']}:{r.get('conclusion','—')}] "
                f"{r['name']} ({r.get('head_branch','?')}) — {r.get('created_at','')[:10]}"
            )
        return "\n".join(lines)

    async def actions_run(
        self,
        owner:     str,
        repo:      str,
        workflow:  str,
        ref:       str  = "main",
        inputs:    dict = None,
    ) -> str:
        payload: dict[str, Any] = {"ref": ref}
        if inputs:
            payload["inputs"] = inputs
        data = await self._post(
            f"/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches",
            payload,
        )
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        return f"✓ Triggered workflow '{workflow}' on ref '{ref}'"

    async def release_create(
        self,
        owner:      str,
        repo:       str,
        tag:        str,
        name:       str  = "",
        body:       str  = "",
        draft:      bool = False,
        prerelease: bool = False,
    ) -> str:
        data = await self._post(f"/repos/{owner}/{repo}/releases", {
            "tag_name":   tag,
            "name":       name or tag,
            "body":       body,
            "draft":      draft,
            "prerelease": prerelease,
        })
        if e := self._err(data):
            return f"ERROR: {e}"
        return f"✓ Release {tag} created: {data.get('html_url', '')}"

    async def release_list(self, owner: str, repo: str, limit: int = 10) -> str:
        data = await self._get(
            f"/repos/{owner}/{repo}/releases",
            {"per_page": min(limit, 30)},
        )
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        if not data:
            return f"No releases in {owner}/{repo}."
        lines = [f"Releases in {owner}/{repo}:"]
        for r in data:
            lines.append(
                f"  {r['tag_name']} — {r.get('name','')} "
                f"({'draft' if r.get('draft') else 'published'}) {r.get('published_at','')[:10]}"
            )
        return "\n".join(lines)

    async def commit_list(
        self,
        owner:  str,
        repo:   str,
        branch: str = "main",
        limit:  int = 20,
    ) -> str:
        data = await self._get(
            f"/repos/{owner}/{repo}/commits",
            {"sha": branch, "per_page": min(limit, 100)},
        )
        if isinstance(data, dict) and self._err(data):
            return f"ERROR: {self._err(data)}"
        if not data:
            return f"No commits on {branch}."
        lines = [f"Commits on {owner}/{repo}/{branch}:"]
        for c in data:
            sha   = c["sha"][:8]
            msg   = c["commit"]["message"].split("\n")[0][:80]
            author = c["commit"]["author"]["name"]
            date   = c["commit"]["author"]["date"][:10]
            lines.append(f"  {sha} ({date}) {author}: {msg}")
        return "\n".join(lines)

    async def commit_diff(self, owner: str, repo: str, sha: str) -> str:
        diff = await self._get_raw(f"/repos/{owner}/{repo}/commits/{sha}")
        if diff.startswith("ERROR"):
            return diff
        return diff[:16_000] + ("\n... (diff truncated)" if len(diff) > 16_000 else "")

    # ── Dispatcher ────────────────────────────────────────────────────────────

    async def execute(self, action: str, **params) -> str:
        if not self._token:
            return (
                "ERROR: GitHub token not configured. "
                "Add GITHUB_TOKEN=ghp_... to your .env file. "
                "Get a token at: github.com/settings/tokens"
            )

        dispatch = {
            "repo_info":      self.repo_info,
            "repo_create":    self.repo_create,
            "branch_list":    self.branch_list,
            "branch_create":  self.branch_create,
            "pr_create":      self.pr_create,
            "pr_list":        self.pr_list,
            "pr_merge":       self.pr_merge,
            "pr_review":      self.pr_review,
            "pr_diff":        self.pr_diff,
            "pr_comments":    self.pr_comments,
            "issue_create":   self.issue_create,
            "issue_list":     self.issue_list,
            "issue_close":    self.issue_close,
            "issue_comment":  self.issue_comment,
            "file_read":      self.file_read,
            "file_write":     self.file_write,
            "search_code":    self.search_code,
            "search_repos":   self.search_repos,
            "actions_list":   self.actions_list,
            "actions_run":    self.actions_run,
            "release_create": self.release_create,
            "release_list":   self.release_list,
            "commit_list":    self.commit_list,
            "commit_diff":    self.commit_diff,
        }

        fn = dispatch.get(action)
        if not fn:
            valid = ", ".join(sorted(dispatch.keys()))
            return f"ERROR: Unknown action '{action}'. Valid actions: {valid}"

        try:
            logger.info(f"[github] {action}({', '.join(f'{k}={str(v)[:30]}' for k, v in params.items())})")
            return await fn(**params)
        except TypeError as exc:
            return f"ERROR: Wrong parameters for '{action}': {exc}"
        except Exception as exc:
            logger.warning(f"[github] {action} failed: {exc}")
            return f"ERROR in github.{action}: {exc}"


# Module-level singleton
github = GitHubTool()
