"""GitHub skill - search repos, get repo info, list issues, create issues.

Uses GitHub REST API. Token optional for read; required for write actions.
"""

import httpx

from openbro.skills.base import BaseSkill
from openbro.tools.base import BaseTool, RiskLevel

GITHUB_API = "https://api.github.com"


def _headers(token: str | None) -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "openbro"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class GitHubTool(BaseTool):
    name = "github"
    description = (
        "Search GitHub repos, get repo details, list/create issues. "
        "Read actions work without auth; create_issue needs github.token in config."
    )
    risk = RiskLevel.MODERATE

    def __init__(self, token: str | None = None):
        self.token = token

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "search_repos",
                            "repo_info",
                            "list_issues",
                            "create_issue",
                        ],
                        "description": "GitHub action to perform",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (for search_repos)",
                    },
                    "repo": {
                        "type": "string",
                        "description": (
                            "Repo in 'owner/name' form (for repo_info, list_issues, create_issue)"
                        ),
                    },
                    "title": {"type": "string", "description": "Issue title (for create_issue)"},
                    "body": {"type": "string", "description": "Issue body (for create_issue)"},
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                    },
                },
                "required": ["action"],
            },
        }

    def run(self, **kwargs) -> str:
        action = kwargs.get("action")
        if action == "search_repos":
            return self._search(kwargs.get("query", ""), kwargs.get("limit", 10))
        if action == "repo_info":
            return self._repo_info(kwargs.get("repo", ""))
        if action == "list_issues":
            return self._list_issues(kwargs.get("repo", ""), kwargs.get("limit", 10))
        if action == "create_issue":
            return self._create_issue(
                kwargs.get("repo", ""),
                kwargs.get("title", ""),
                kwargs.get("body", ""),
            )
        return f"Unknown action: {action}"

    def _search(self, query: str, limit: int) -> str:
        if not query:
            return "Query required."
        try:
            r = httpx.get(
                f"{GITHUB_API}/search/repositories",
                params={"q": query, "per_page": limit},
                headers=_headers(self.token),
                timeout=10,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                return f"No repos found for: {query}"
            lines = []
            for it in items[:limit]:
                desc = it.get("description") or ""
                lines.append(f"- {it['full_name']} (★{it['stargazers_count']}) — {desc}")
            return "\n".join(lines)
        except Exception as e:
            return f"GitHub search failed: {e}"

    def _repo_info(self, repo: str) -> str:
        if not repo or "/" not in repo:
            return "Repo must be in 'owner/name' form."
        try:
            r = httpx.get(
                f"{GITHUB_API}/repos/{repo}",
                headers=_headers(self.token),
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            return (
                f"{d['full_name']}\n"
                f"  ★ {d['stargazers_count']} | forks: {d['forks_count']} | "
                f"issues: {d['open_issues_count']}\n"
                f"  Lang: {d.get('language') or 'N/A'} | License: "
                f"{(d.get('license') or {}).get('name') or 'None'}\n"
                f"  {d.get('description') or ''}\n"
                f"  {d['html_url']}"
            )
        except Exception as e:
            return f"Failed to fetch repo: {e}"

    def _list_issues(self, repo: str, limit: int) -> str:
        if not repo or "/" not in repo:
            return "Repo must be in 'owner/name' form."
        try:
            r = httpx.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                params={"per_page": limit, "state": "open"},
                headers=_headers(self.token),
                timeout=10,
            )
            r.raise_for_status()
            issues = [i for i in r.json() if "pull_request" not in i]
            if not issues:
                return f"No open issues in {repo}."
            lines = [f"#{i['number']} {i['title']}" for i in issues[:limit]]
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to list issues: {e}"

    def _create_issue(self, repo: str, title: str, body: str) -> str:
        if not self.token:
            return "github.token required in config to create issues."
        if not repo or not title:
            return "repo and title required."
        try:
            r = httpx.post(
                f"{GITHUB_API}/repos/{repo}/issues",
                headers=_headers(self.token),
                json={"title": title, "body": body},
                timeout=10,
            )
            r.raise_for_status()
            d = r.json()
            return f"Created issue #{d['number']}: {d['html_url']}"
        except Exception as e:
            return f"Failed to create issue: {e}"


class GitHubSkill(BaseSkill):
    name = "github"
    description = "Search repos, get repo info, list and create issues on GitHub."
    version = "0.1.0"
    author = "openbro"
    config_keys: list[str] = []  # token optional

    def is_configured(self) -> bool:
        return True  # works anonymously for read

    def tools(self) -> list[BaseTool]:
        token = self._get_nested(self.config, "skills.github.token")
        return [GitHubTool(token=token)]
